"""Deterministic full-chain demo of Sub-phase 4E — no Kafka, no DB, no network.

It wires the *real* pieces end to end:

    incident.opened  (the exact Phase 3 lifecycle envelope)
        -> IncidentEventConsumer            (rca-agent Kafka handler)
        -> InvestigationService.investigate (begin -> LangGraph -> complete)
        -> read-only evidence tools         (against an in-process fake Incident API)
        -> MockLlmClient                    (deterministic reasoner)
        -> validate_report                  (deterministic safety gate)
        -> InMemoryInvestigationRepository
        -> GET /investigations/{id}         (the real FastAPI app)

    python scripts/rca_e2e_scenario.py

Expected: the consumer is idempotent (a redelivered event is a no-op), the
investigation reaches a validated terminal state with evidence-grounded findings,
and the recommended action ALWAYS requires human approval (Phase 5 owns
execution).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import ClassVar

import httpx
from fastapi.testclient import TestClient

from rca_agent.app import create_app
from rca_agent.config import Settings
from rca_agent.engine import InvestigationService
from rca_agent.kafka.consumer import IncidentEventConsumer
from rca_agent.metrics import get_metrics
from rca_agent.repository import InMemoryInvestigationRepository
from rca_agent.tools import build_registry
from sentinelops_common.contracts import (
    INCIDENT_LIFECYCLE_VERSION,
    INCIDENT_OPENED,
    IncidentLifecycleV1,
)
from sentinelops_common.events import EventEnvelope
from sentinelops_common.kafka import KafkaJsonProducer

_INCIDENT_ID = "inc_0e2e0e2e0e2e"
_SERVICE = "orders-service"
_ABNORMAL = ["error_rate", "latency_p95_ms"]

_INCIDENT = {
    "id": _INCIDENT_ID,
    "correlation_key": f"{_SERVICE}:development",
    "service": _SERVICE,
    "environment": "development",
    "status": "OPEN",
    "severity": "HIGH",
    "title": f"HIGH - {', '.join(_ABNORMAL)} in {_SERVICE} (development)",
    "anomaly_count": 4,
    "distinct_abnormal_signals": 2,
    "started_at": "2026-09-01T12:00:00Z",
    "last_evidence_at": "2026-09-01T12:04:00Z",
    "created_at": "2026-09-01T12:01:00Z",
    "updated_at": "2026-09-01T12:04:00Z",
    "resolved_at": None,
    "acknowledged_at": None,
    "resolution": None,
    "severity_reasons": ["error rate 35% >= 30%"],
    "abnormal_signal_names": _ABNORMAL,
    "max_anomaly_score": 0.93,
    "max_error_rate": 0.35,
    "max_latency_p95_ms": 780.0,
    "detector": "isolation_forest",
    "duration_seconds": 240.0,
    "evidence": [],
    "history": [],
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


def _incident_api(request: httpx.Request) -> httpx.Response:
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
    return httpx.Response(404, content=json.dumps({"detail": "not found"}))


def _incident_opened_envelope() -> EventEnvelope:
    payload = IncidentLifecycleV1(
        incident_id=_INCIDENT_ID,
        correlation_key=f"{_SERVICE}:development",
        service=_SERVICE,
        environment="development",
        status="OPEN",
        severity="HIGH",
        anomaly_count=4,
        title=str(_INCIDENT["title"]),
        started_at="2026-09-01T12:00:00+00:00",
        updated_at="2026-09-01T12:04:00+00:00",
        change="opened",
    )
    return EventEnvelope(
        event_type=INCIDENT_OPENED,
        event_version=INCIDENT_LIFECYCLE_VERSION,
        source="incident-correlator",
        payload=payload.model_dump(),
    )


class _NoHeaderRecord:
    headers: ClassVar[list[tuple[str, bytes]]] = []


def _quiet() -> None:
    for name in ("httpx", "httpcore", "opentelemetry", "rca_agent", "aiokafka"):
        logging.getLogger(name).setLevel(logging.WARNING)


async def _run() -> InMemoryInvestigationRepository:
    settings = Settings()  # RCA_MODE=mock
    repo = InMemoryInvestigationRepository()
    http = httpx.AsyncClient(transport=httpx.MockTransport(_incident_api))
    registry = build_registry(settings, http_client=http)
    from rca_agent.llm import build_llm_client

    service = InvestigationService(
        repository=repo,
        registry=registry,
        llm_client=build_llm_client(settings),
        settings=settings,
    )
    consumer = IncidentEventConsumer(
        settings,
        service,
        dlq_producer=KafkaJsonProducer("localhost:0", client_id="e2e"),
        metrics=get_metrics(),
    )

    env = _incident_opened_envelope()
    print(f"1. incident.opened  incident_id={_INCIDENT_ID}  change=opened")
    await consumer.handle(env, _NoHeaderRecord())
    inv = await repo.get_latest_investigation(_INCIDENT_ID)
    assert inv is not None
    print(f"   -> investigation {inv.id}  status={inv.status}  trigger={inv.trigger}")

    print("2. redelivered incident.opened (at-least-once)")
    await consumer.handle(env, _NoHeaderRecord())
    again = await repo.get_latest_investigation(_INCIDENT_ID)
    assert again is not None and again.id == inv.id
    print(f"   -> still {again.id} (no second investigation)")

    await http.aclose()
    return repo


def _report(repo: InMemoryInvestigationRepository) -> None:
    app = create_app(
        Settings(),
        repository=repo,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_incident_api)),
        run_consumer=False,
    )
    with TestClient(app) as client:
        got = client.get(f"/incidents/{_INCIDENT_ID}/investigation")
        assert got.status_code == 200, got.text
        detail = got.json()
        inv, report = detail["investigation"], detail["report"]

        print("\n3. GET /incidents/{id}/investigation")
        print(f"   status            {inv['status']}")
        print(f"   tool calls        {inv['tool_call_count']}")
        print(f"   evidence items    {inv['evidence_count']}")
        print(f"   trace steps       {len(detail['steps'])}")

        assert report is not None, "expected a structured RCA report"
        print("\n4. RCA report")
        print(f"   summary           {report['summary']}")
        rc = report["root_cause"]
        if rc:
            print(f"   root cause        {rc['statement']}")
            print(f"     confidence      {rc['confidence']}")
            print(f"     evidence        {rc['evidence_ids']}")
        else:
            print("   root cause        UNDETERMINED (honest — insufficient evidence)")
        ra = report["recommended_action"]
        print(f"   recommendation    {ra['action_type']} (target={ra['target_service']})")
        print(f"     requires human approval: {ra['requires_human_approval']}")
        print(
            "   unavailable sources: "
            f"{[s.split(':')[0] for s in report['unavailable_evidence_sources']]}"
        )

        by_id = client.get(f"/investigations/{inv['id']}")
        assert by_id.status_code == 200

        known = {e["id"] for e in report["evidence"]}
        cited = {i for f in report["findings"] for i in f["evidence_ids"]}
        assert cited <= known, "every cited evidence id was collected this investigation"
        assert ra["requires_human_approval"] is True

    print("\nOK: incident.opened -> idempotent consumer -> validated, evidence-grounded RCA.")


if __name__ == "__main__":
    _quiet()
    _report(asyncio.run(_run()))
