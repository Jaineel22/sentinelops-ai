"""Deterministic full-chain Phase 5 (5A-5G) demo - no Kafka broker, DB, or network.

It wires the *real* components end to end:

    incident.opened  (the exact Phase 3 lifecycle envelope)
        -> IncidentEventConsumer            (rca-agent Kafka handler)
        -> InvestigationService             (bounded LangGraph, mock reasoner)
        -> RCAReport                        (evidence-grounded, requires_human_approval)
        -> POST /remediations               (remediation-controller FastAPI)
        -> deterministic PolicyEngine       (5B)
        -> PENDING_APPROVAL
        -> explicit human approval          (5C — identity + role + reason)
        -> POST /remediations/{id}/execute  (5D — LocalSimulationExecutor, SIMULATION)
        -> append-only audit trail          (5E)
        -> POST /remediations/{id}/verify-recovery  (5F — observe-only)
        -> RECOVERED | RECOVERY_FAILED
        -> remediation.events lifecycle events (5G — captured in-memory here)

    python scripts/remediation_e2e_scenario.py

There is no AI -> auto-approval -> execution path. The RCA's own machine
recommendation is a *human-decision* category; a human then picks an allow-listed
action. Execution is a LOCAL SIMULATION — nothing real is touched.
"""

from __future__ import annotations

# ruff: noqa: E402  (a source-checkout path shim must precede the package imports)
import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

# The monorepo ships one distribution with several import packages; make the demo
# runnable straight from a source checkout (mirrors pyproject's pytest pythonpath).
_ROOT = Path(__file__).resolve().parent.parent
for _pkg_dir in (
    "libs",
    "services/rca-agent",
    "services/remediation-controller",
    "services/incident-correlator",
):
    _p = str(_ROOT / _pkg_dir)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import httpx
from fastapi.testclient import TestClient

from rca_agent.config import Settings as RcaSettings
from rca_agent.engine import InvestigationService
from rca_agent.kafka.consumer import IncidentEventConsumer
from rca_agent.llm import build_llm_client
from rca_agent.metrics import get_metrics as rca_metrics
from rca_agent.repository import InMemoryInvestigationRepository
from rca_agent.tools import build_registry
from remediation_controller import SERVICE_NAME
from remediation_controller.app import create_app
from remediation_controller.config import AppSettings, Settings
from remediation_controller.executor.simulation import LocalSimulationExecutor, SimulationState
from remediation_controller.kafka.publisher import RemediationEventPublisher
from remediation_controller.metrics import get_metrics
from remediation_controller.recovery.config import RecoveryVerificationConfig
from remediation_controller.repository import InMemoryRemediationRepository
from sentinelops_common.contracts import (
    INCIDENT_LIFECYCLE_VERSION,
    INCIDENT_OPENED,
    IncidentLifecycleV1,
)
from sentinelops_common.events import EventEnvelope
from sentinelops_common.kafka import KafkaJsonProducer

_INCIDENT_ID = "inc_5e2e5e2e5e2e"
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
    value = b""
    key = None
    offset = 0


@dataclass
class _CapturingProducer:
    """Stands in for a real Kafka producer — records every lifecycle event."""

    started: bool = True
    messages: list[tuple[str, str, EventEnvelope]] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.started

    async def publish(
        self,
        topic: str,
        envelope: EventEnvelope,
        *,
        key: str,
        extra_headers: list[tuple[str, bytes]] | None = None,
    ) -> None:
        self.messages.append((topic, key, envelope))


def _quiet() -> None:
    logging.disable(logging.INFO)
    for name in (
        "httpx",
        "httpx2",
        "httpcore",
        "opentelemetry",
        "rca_agent",
        "aiokafka",
        "remediation_controller",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


async def _run_rca() -> tuple[Any, Any]:
    settings = RcaSettings()  # RCA_MODE=mock
    repo = InMemoryInvestigationRepository()
    http = httpx.AsyncClient(transport=httpx.MockTransport(_incident_api))
    service = InvestigationService(
        repository=repo,
        registry=build_registry(settings, http_client=http),
        llm_client=build_llm_client(settings),
        settings=settings,
    )
    consumer = IncidentEventConsumer(
        settings,
        service,
        dlq_producer=KafkaJsonProducer("localhost:0", client_id="e2e"),  # never started
        metrics=rca_metrics(),
    )
    env = _incident_opened_envelope()
    print(f"1. incident.opened            incident_id={_INCIDENT_ID}")
    await consumer.handle(env, _NoHeaderRecord())
    print("2. redelivered incident.opened (at-least-once) -> idempotent, no 2nd investigation")
    await consumer.handle(env, _NoHeaderRecord())
    inv = await repo.get_latest_investigation(_INCIDENT_ID)
    assert inv is not None
    report = await repo.get_report(inv.id)
    assert report is not None
    await http.aclose()

    rc = report.root_cause
    ra = report.recommended_action
    print(f"   -> investigation {inv.id}  status={inv.status}")
    print(f"   -> root cause      {rc.statement if rc else 'UNDETERMINED'}")
    print(
        f"   -> RCA recommends  {ra.action_type} "
        f"(requires_human_approval={ra.requires_human_approval})"
    )
    return inv, report


def _client(producer: _CapturingProducer, *, state: SimulationState | None = None) -> TestClient:
    publisher = RemediationEventPublisher(
        producer, topic="remediation.events", source=SERVICE_NAME, metrics=get_metrics()
    )
    app = create_app(
        Settings(app=AppSettings(log_level="WARNING")),
        repository=InMemoryRemediationRepository(),
        executor=LocalSimulationExecutor(state) if state is not None else None,
        verify_config=RecoveryVerificationConfig(timeout_seconds=4, poll_interval_seconds=1.0),
        run_publisher=False,
        event_publisher=publisher,
    )
    return TestClient(app)


def _operator_body(report: Any, inv: Any) -> dict[str, Any]:
    root = report.root_cause.statement if report.root_cause else "n/a"
    return {
        "incident_id": _INCIDENT_ID,
        "investigation_id": inv.id if inv.id.startswith("rca_") else None,
        "incident_severity": "HIGH",
        "recommended_action": {
            "action_type": "RESTART_SERVICE",
            "target_service": "orders-service",
            "description": "Operator: RCA points to a service-side saturation a restart clears.",
            "rationale": f"RCA root cause: {root}",
            "evidence_ids": [e.id for e in report.evidence][:3],
        },
    }


def _happy_path(report: Any, inv: Any) -> None:
    producer = _CapturingProducer()
    with _client(producer) as client:
        rca_body = {
            "incident_id": _INCIDENT_ID,
            "incident_severity": "HIGH",
            "recommended_action": {
                "action_type": str(report.recommended_action.action_type),
                "target_service": "orders-service",
                "description": report.recommended_action.description,
                "rationale": report.recommended_action.rationale,
            },
        }
        r = client.post("/remediations", json=rca_body)
        print(
            f"\n3. POST /remediations (RCA's own recommendation)  -> {r.status_code} "
            f"({r.json()['detail'][:60]}...)"
        )

        r = client.post("/remediations", json=_operator_body(report, inv))
        rid = r.json()["remediation_id"]
        print(
            f"4. POST /remediations (operator: RESTART_SERVICE) -> {r.status_code} "
            f"status={r.json()['status']}  id={rid}"
        )

        r = client.post(f"/remediations/{rid}/execute", json={})
        print(
            f"5. POST /execute BEFORE approval                 -> {r.status_code} "
            f"(human-in-the-loop guard)"
        )

        r = client.post(
            f"/remediations/{rid}/approve",
            json={
                "approver_identity": "oncall@example.com",
                "approver_role": "ADMINISTRATOR",
                "reason": "Reviewed the RCA; a restart is the minimal safe action.",
            },
        )
        print(
            f"6. POST /approve (ADMINISTRATOR)                 -> {r.status_code} "
            f"status={r.json()['status']}"
        )

        r = client.post(f"/remediations/{rid}/execute", json={})
        ex = r.json()["execution"]
        print(
            f"7. POST /execute                                -> {r.status_code} "
            f"status={r.json()['status']}  executor={ex['executor_type']}  [SIMULATION]"
        )
        print(f"   simulated effect: {ex['simulated_effect']}")

        r = client.post(f"/remediations/{rid}/verify-recovery", json={})
        v = r.json()["verification"]
        print(
            f"8. POST /verify-recovery                        -> {r.status_code} "
            f"status={r.json()['status']}  attempts={v['attempts']}  verifier={v['verifier_type']}"
        )

        audit = client.get(f"/remediations/{rid}/audit").json()
        print(
            f"9. GET /audit  ({audit['count']} immutable events): "
            f"{[e['event_type'] for e in audit['events']]}"
        )

        # idempotency
        dup_exec = client.post(f"/remediations/{rid}/execute", json={}).status_code
        dup_ver = client.post(f"/remediations/{rid}/verify-recovery", json={})
        replayed = dup_ver.json()["verification"]["verification_id"] == v["verification_id"]
        print(
            f"10. duplicate /execute -> {dup_exec}   "
            f"duplicate /verify-recovery -> {dup_ver.status_code} (replayed={replayed})"
        )

    print(f"11. lifecycle events published ({len(producer.messages)}), key = remediation_id:")
    for topic, key, env in producer.messages:
        print(f"    {topic}  key={key}  {env.event_type}  ({env.payload['new_state']})")


def _rejection_and_failure_paths(report: Any, inv: Any) -> None:
    producer = _CapturingProducer()
    with _client(producer) as client:
        rid = client.post("/remediations", json=_operator_body(report, inv)).json()[
            "remediation_id"
        ]
        client.post(
            f"/remediations/{rid}/reject",
            json={"approver_identity": "sre@x", "approver_role": "OPERATOR", "reason": "not now"},
        )
        after = client.post(f"/remediations/{rid}/execute", json={}).status_code
        print(f"\n12. rejection path: REJECTED then /execute -> {after} (rejected never runs)")

    chronic = SimulationState()
    chronic.inject_fault("orders-service", chronic=True)
    producer2 = _CapturingProducer()
    with _client(producer2, state=chronic) as client:
        rid = client.post("/remediations", json=_operator_body(report, inv)).json()[
            "remediation_id"
        ]
        client.post(
            f"/remediations/{rid}/approve",
            json={"approver_identity": "a@x", "approver_role": "ADMINISTRATOR"},
        )
        client.post(f"/remediations/{rid}/execute", json={})
        r = client.post(f"/remediations/{rid}/verify-recovery", json={})
        reason = r.json()["verification"]["failure_reason"][:70]
        print(
            f"13. recovery-failed path: chronic fault -> verify-recovery -> "
            f"{r.status_code} status={r.json()['status']}  reason={reason}"
        )
    print(f"    last lifecycle event: {producer2.messages[-1][2].event_type}")


def main() -> None:
    _quiet()
    inv, report = asyncio.run(_run_rca())
    _happy_path(report, inv)
    _rejection_and_failure_paths(report, inv)
    print(
        "\nOK: incident -> RCA -> human-approved, allow-listed, simulated remediation "
        "-> audit -> recovery verification -> lifecycle events."
    )


if __name__ == "__main__":
    main()
