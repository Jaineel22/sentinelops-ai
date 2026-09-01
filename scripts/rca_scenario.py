"""End-to-end demo of the Phase 4 AI RCA engine (Sub-phase 4C / 4D).

It wires the *real* investigation graph (LangGraph) + the real read-only tool
layer against an in-memory Incident API fake, runs one investigation, and prints
the structured RCA. The reasoner is chosen by ``build_llm_client``:

    python scripts/rca_scenario.py
        -> RCA_MODE=mock (default): deterministic, no network, no API key.

    RCA_MODE=live LLM_PROVIDER=anthropic LLM_API_KEY=sk-ant-... \\
        python scripts/rca_scenario.py
        -> a live smoke test of AnthropicLlmClient against the fake incident
           data. The key is read from the environment and never printed.

Expected either way: the graph plans, calls read-only evidence tools, forms and
verifies hypotheses, synthesizes a root cause backed by evidence ids, and the
report passes deterministic validation. A recommendation is produced that ALWAYS
requires human approval (Phase 5 owns execution).
"""

from __future__ import annotations

import asyncio
import json

import httpx

from rca_agent.config import Settings
from rca_agent.domain import InvestigationTrigger
from rca_agent.engine import InvestigationService
from rca_agent.llm import build_llm_client
from rca_agent.repository import InMemoryInvestigationRepository
from rca_agent.tools import build_registry

# The scenario: a sustained latency + error-rate incident on orders-service.
_INCIDENT_ID = "inc_00112233aabbccdd"
_ABNORMAL = ["error_rate", "latency_p95_ms"]

_INCIDENT = {
    "id": _INCIDENT_ID,
    "correlation_key": "orders-service:development",
    "service": "orders-service",
    "environment": "development",
    "status": "OPEN",
    "severity": "HIGH",
    "title": "HIGH - error_rate, latency_p95_ms in orders-service (development)",
    "anomaly_count": 4,
    "distinct_abnormal_signals": 2,
    "started_at": "2026-09-01T12:00:00Z",
    "last_evidence_at": "2026-09-01T12:04:00Z",
    "created_at": "2026-09-01T12:01:00Z",
    "updated_at": "2026-09-01T12:04:00Z",
    "severity_reasons": ["error rate 35% >= 30%"],
    "abnormal_signal_names": _ABNORMAL,
    "max_anomaly_score": 0.93,
    "max_error_rate": 0.35,
    "max_latency_p95_ms": 780.0,
    "detector": "isolation_forest",
    "duration_seconds": 240.0,
}
_ANOMALIES = [
    {
        "event_id": f"evt-{i}",
        "detector": "isolation_forest",
        "detector_version": "0.3.0",
        "anomaly_score": 0.93,
        "threshold": 0.5,
        "window_start": f"2026-09-01T12:0{i}:00Z",
        "window_end": f"2026-09-01T12:0{i}:10Z",
        "signals": {"error_rate": 0.35, "latency_p95_ms": 780.0},
        "abnormal_signals": _ABNORMAL,
        "trace_id": None,
        "occurred_at": f"2026-09-01T12:0{i}:10Z",
        "correlation_reason": "within correlation window (gap 10s <= 300s)",
    }
    for i in range(4)
]
_HISTORY = [
    {
        "from_status": None,
        "to_status": "OPEN",
        "actor": "system",
        "reason": "opened",
        "severity_at_transition": "LOW",
        "created_at": "2026-09-01T12:01:00Z",
    }
]
_METRICS = (
    "# TYPE orders_created_total counter\norders_created_total 41.0\n"
    'orders_request_failed_total{reason="server_error"} 12.0\n'
)


def _handler(request: httpx.Request) -> httpx.Response:
    def ok(payload: object) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(payload))

    path = request.url.path
    if path == f"/incidents/{_INCIDENT_ID}":
        return ok(_INCIDENT)
    if path == f"/incidents/{_INCIDENT_ID}/evidence":
        return ok(_ANOMALIES)
    if path == f"/incidents/{_INCIDENT_ID}/history":
        return ok(_HISTORY)
    if path == "/incidents":
        return ok([])
    if path == "/metrics":
        return httpx.Response(200, text=_METRICS)
    if path in ("/health", "/ready"):
        return ok({"status": "ok"})
    return httpx.Response(404)


async def _run() -> None:
    settings = Settings()  # RCA_MODE=mock by default; RCA_MODE=live -> real provider
    registry = build_registry(
        settings, http_client=httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    )
    print(f"reasoner: RCA_MODE={settings.rca.mode} provider={settings.llm.provider}")
    service = InvestigationService(
        repository=InMemoryInvestigationRepository(),
        registry=registry,
        llm_client=build_llm_client(settings),
        settings=settings,
    )

    outcome = await service.investigate(_INCIDENT_ID, trigger=InvestigationTrigger.MANUAL)
    inv, report = outcome.investigation, outcome.report

    print("=== investigation ===")
    print(f"  id                {inv.id}")
    print(f"  status            {inv.status}")
    print(f"  tool calls        {inv.tool_call_count}")
    print(f"  steps             {inv.step_count}")
    print(f"  evidence items    {inv.evidence_count}")
    print(f"  termination       {inv.termination_reason}")

    print("\n=== operational trace ===")
    for s in outcome.steps:
        tool = f" [{s.tool_name}]" if s.tool_name else ""
        print(f"  {s.seq:2d}. {s.kind:<12}{tool}  {s.description}")

    if report is None:
        print("\n(no RCA report was produced)")
        return

    print("\n=== RCA report ===")
    print(f"  status            {report.status}")
    print(f"  overall confidence {report.overall_confidence}")
    print(f"  summary           {report.summary}")
    if report.root_cause:
        rc = report.root_cause
        print(f"  root cause        {rc.statement}")
        print(f"    confidence      {rc.confidence}")
        print(f"    evidence        {rc.evidence_ids}")
    else:
        print("  root cause        UNDETERMINED (honest — insufficient evidence)")
    print(f"  hypotheses        {[(h.id, str(h.verdict)) for h in report.hypotheses]}")
    print(f"  uncertainty       {report.uncertainty}")
    ra = report.recommended_action
    print(f"  recommendation    {ra.action_type} (target={ra.target_service})")
    print(f"    requires human approval: {ra.requires_human_approval}")
    unavail = [s.split(":")[0] for s in report.unavailable_evidence_sources]
    print(f"  unavailable sources: {unavail}")

    known = {e.id for e in report.evidence}
    cited = {i for f in report.findings for i in f.evidence_ids}
    assert cited <= known, "every cited evidence id was collected this investigation"
    print("\nOK: RCA is evidence-grounded, validated, and human-approval-gated.")


if __name__ == "__main__":
    asyncio.run(_run())
