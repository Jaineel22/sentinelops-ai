"""Phase 5H: the complete SentinelOps remediation lifecycle, end to end.

Deterministic and in-process — no external Kafka / DB / network. It wires the
*real* components through their established interfaces:

    incident.opened (Phase 3 envelope)
      -> rca-agent IncidentEventConsumer -> InvestigationService -> RCAReport
      -> [human, informed by the RCA] POST /remediations   (remediation-controller)
      -> deterministic policy evaluation -> PENDING_APPROVAL
      -> explicit human approval (identity + role + reason) -> APPROVED
      -> POST /execute -> LocalSimulationExecutor -> EXECUTED   (SIMULATION)
      -> append-only audit trail
      -> POST /verify-recovery -> RECOVERED | RECOVERY_FAILED
      -> remediation.events lifecycle events

The RCA's own machine recommendation is a *human-decision* category
(``INVESTIGATE_FURTHER`` / ``CONTACT_SERVICE_OWNER``); feeding it straight to
``POST /remediations`` is correctly refused. A human then selects an
allow-listed action. There is no AI -> auto-approval -> execution path.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

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
from remediation_controller.config import Settings
from remediation_controller.executor.simulation import LocalSimulationExecutor, SimulationState
from remediation_controller.kafka.publisher import RemediationEventPublisher
from remediation_controller.metrics import get_metrics
from remediation_controller.recovery.config import RecoveryVerificationConfig
from remediation_controller.repository import InMemoryRemediationRepository
from sentinelops_common.contracts import RemediationLifecycleV1
from sentinelops_common.kafka import KafkaJsonProducer
from tests.rca_agent.incident_api_fakes import INCIDENT_ID, make_anomaly_windows, scenario_handler
from tests.rca_agent.kafka_fakes import FakeRecord, incident_lifecycle_envelope
from tests.remediation_controller.kafka_fakes import FakeKafkaProducer

_FAST = RecoveryVerificationConfig(timeout_seconds=4, poll_interval_seconds=1.0)


# --------------------------------------------------------------------- RCA half
async def _run_rca() -> Any:
    """incident.opened -> a real (mock-LLM) RCA investigation. Returns the report."""

    settings = RcaSettings()  # RCA_MODE=mock -> deterministic, no network
    repo = InMemoryInvestigationRepository()
    handler = scenario_handler(
        anomalies=make_anomaly_windows(count=4, abnormal=["error_rate", "latency_p95_ms"])
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = InvestigationService(
        repository=repo,
        registry=build_registry(settings, http_client=http),
        llm_client=build_llm_client(settings),
        settings=settings,
    )
    consumer = IncidentEventConsumer(
        settings,
        service,
        dlq_producer=KafkaJsonProducer("localhost:0", client_id="e2e"),
        metrics=rca_metrics(),
    )
    await consumer.handle(incident_lifecycle_envelope(change="opened"), FakeRecord())
    # redelivery is a no-op (idempotent consumer)
    await consumer.handle(incident_lifecycle_envelope(change="opened"), FakeRecord())
    inv = await repo.get_latest_investigation(INCIDENT_ID)
    assert inv is not None
    report = await repo.get_report(inv.id)
    await http.aclose()
    assert report is not None
    return inv, report


# ------------------------------------------------------------- remediation half
@contextmanager
def _remediation_client(
    *, producer: FakeKafkaProducer, state: SimulationState | None = None
) -> Iterator[TestClient]:
    publisher = RemediationEventPublisher(
        producer, topic="remediation.events", source=SERVICE_NAME, metrics=get_metrics()
    )
    app = create_app(
        Settings(),
        repository=InMemoryRemediationRepository(),
        executor=LocalSimulationExecutor(state) if state is not None else None,
        verify_config=_FAST,
        run_publisher=False,
        event_publisher=publisher,
    )
    with TestClient(app) as client:
        yield client


def _operator_remediation_body(report: Any, inv: Any) -> dict[str, Any]:
    """A human operator, having read the RCA, selects an allow-listed action."""

    ra = report.recommended_action
    evidence_ids = [e.id for e in report.evidence][:3]
    inv_id = inv.id if inv.id.startswith("rca_") else None
    root = report.root_cause.statement if report.root_cause else "n/a"
    return {
        "incident_id": INCIDENT_ID,
        "investigation_id": inv_id,
        "incident_severity": "HIGH",
        "recommended_action": {
            "action_type": "RESTART_SERVICE",  # operator's choice, from the closed catalogue
            "target_service": ra.target_service or "orders-service",
            "description": "Operator: RCA indicates a service-side saturation a restart clears.",
            "rationale": f"RCA root cause: {root}",
            "evidence_ids": evidence_ids,
        },
    }


async def test_full_lifecycle_incident_to_recovered() -> None:
    inv, report = await _run_rca()
    assert report.recommended_action.requires_human_approval is True
    producer = FakeKafkaProducer()

    with _remediation_client(producer=producer) as client:
        # 1. the RCA's OWN recommendation category is not executable -> refused
        rca_body = {
            "incident_id": INCIDENT_ID,
            "incident_severity": "HIGH",
            "recommended_action": {
                "action_type": str(report.recommended_action.action_type),
                "target_service": report.recommended_action.target_service or "orders-service",
                "description": report.recommended_action.description,
                "rationale": report.recommended_action.rationale,
            },
        }
        assert client.post("/remediations", json=rca_body).status_code == 422

        # 2. a human operator selects an allow-listed action informed by the RCA
        created = client.post("/remediations", json=_operator_remediation_body(report, inv))
        assert created.status_code == 201, created.text
        rid = created.json()["remediation_id"]
        assert created.json()["status"] == "PENDING_APPROVAL"

        # 3. human-in-the-loop: execution is impossible before approval
        assert client.post(f"/remediations/{rid}/execute", json={}).status_code == 409

        # 4. explicit human approval (identity + role + reason, persisted)
        appr = client.post(
            f"/remediations/{rid}/approve",
            json={
                "approver_identity": "oncall@example.com",
                "approver_role": "ADMINISTRATOR",
                "reason": "Reviewed the RCA; a restart is the minimal safe action.",
            },
        )
        assert appr.status_code == 200
        assert appr.json()["status"] == "APPROVED"
        assert appr.json()["approval"]["approver_identity"] == "oncall@example.com"

        # 5. simulated execution
        ex = client.post(f"/remediations/{rid}/execute", json={})
        assert ex.status_code == 200
        assert ex.json()["status"] == "EXECUTED"
        assert ex.json()["execution"]["executor_type"] == "LOCAL_SIMULATION"
        assert ex.json()["execution"]["status"] == "SUCCEEDED"

        # 6. append-only audit trail is complete
        audit = client.get(f"/remediations/{rid}/audit").json()
        kinds = [e["event_type"] for e in audit["events"]]
        assert kinds == [
            "PROPOSAL_CREATED",
            "POLICY_EVALUATED",
            "APPROVED",
            "EXECUTION_REQUESTED",
            "EXECUTION_STARTED",
            "EXECUTION_SUCCEEDED",
        ]

        # 7. recovery verification (observe-only)
        ver = client.post(f"/remediations/{rid}/verify-recovery", json={})
        assert ver.status_code == 200
        assert ver.json()["status"] == "RECOVERED"
        assert ver.json()["verification"]["verifier_type"] == "DETERMINISTIC_LOCAL"

    # 8. lifecycle events, ordered, keyed by remediation_id, contract-valid
    assert producer.event_types() == [
        "remediation.proposed",
        "remediation.policy_evaluated",
        "remediation.approved",
        "remediation.execution_started",
        "remediation.execution_succeeded",
        "remediation.recovery_verification_started",
        "remediation.recovered",
    ]
    assert set(producer.keys()) == {rid}
    for msg in producer.messages:
        RemediationLifecycleV1.model_validate(msg.envelope.payload)


async def test_idempotency_and_concurrency_guards() -> None:
    inv, report = await _run_rca()
    producer = FakeKafkaProducer()
    with _remediation_client(producer=producer) as client:
        rid = client.post("/remediations", json=_operator_remediation_body(report, inv)).json()[
            "remediation_id"
        ]

        ok = client.post(
            f"/remediations/{rid}/approve",
            json={"approver_identity": "a@x", "approver_role": "ADMINISTRATOR"},
        )
        assert ok.status_code == 200
        # duplicate approval -> 409, no second decision
        assert (
            client.post(
                f"/remediations/{rid}/approve",
                json={"approver_identity": "b@x", "approver_role": "ADMINISTRATOR"},
            ).status_code
            == 409
        )

        assert client.post(f"/remediations/{rid}/execute", json={}).status_code == 200
        # duplicate execution -> 409, no second execution
        assert client.post(f"/remediations/{rid}/execute", json={}).status_code == 409

        first = client.post(f"/remediations/{rid}/verify-recovery", json={})
        assert first.status_code == 200
        events_before = len(producer.messages)
        second = client.post(f"/remediations/{rid}/verify-recovery", json={})
        assert second.status_code == 200
        assert (
            second.json()["verification"]["verification_id"]
            == first.json()["verification"]["verification_id"]
        )
        assert len(producer.messages) == events_before  # no duplicate lifecycle event

        kinds = [e["event_type"] for e in client.get(f"/remediations/{rid}/audit").json()["events"]]
        assert kinds.count("APPROVED") == 1
        assert kinds.count("EXECUTION_STARTED") == 1
        assert kinds.count("VERIFICATION_STARTED") == 1


async def test_rejected_remediation_cannot_execute() -> None:
    inv, report = await _run_rca()
    producer = FakeKafkaProducer()
    with _remediation_client(producer=producer) as client:
        rid = client.post("/remediations", json=_operator_remediation_body(report, inv)).json()[
            "remediation_id"
        ]

        rej = client.post(
            f"/remediations/{rid}/reject",
            json={
                "approver_identity": "sre@x",
                "approver_role": "OPERATOR",
                "reason": "too risky now",
            },
        )
        assert rej.status_code == 200
        assert rej.json()["status"] == "REJECTED"
        assert client.post(f"/remediations/{rid}/execute", json={}).status_code == 409

    assert "remediation.rejected" in producer.event_types()
    assert "remediation.execution_started" not in producer.event_types()


async def test_recovery_failed_path() -> None:
    inv, report = await _run_rca()
    state = SimulationState()
    state.inject_fault("orders-service", chronic=True)  # a restart will not fix it
    producer = FakeKafkaProducer()
    with _remediation_client(producer=producer, state=state) as client:
        rid = client.post("/remediations", json=_operator_remediation_body(report, inv)).json()[
            "remediation_id"
        ]
        client.post(
            f"/remediations/{rid}/approve",
            json={"approver_identity": "a@x", "approver_role": "ADMINISTRATOR"},
        )
        client.post(f"/remediations/{rid}/execute", json={})
        ver = client.post(f"/remediations/{rid}/verify-recovery", json={})
        assert ver.status_code == 200
        assert ver.json()["status"] == "RECOVERY_FAILED"
        assert ver.json()["verification"]["failure_reason"]

    assert producer.event_types()[-1] == "remediation.recovery_failed"
