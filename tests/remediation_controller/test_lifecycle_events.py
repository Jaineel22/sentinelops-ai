"""Phase 5G: representative lifecycle transitions emit the expected Kafka events.

Uses the real RemediationService over the in-memory repository + a fake producer
— the same wiring the app uses, minus a live broker.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from remediation_controller import SERVICE_NAME
from remediation_controller.domain import ApprovalDecision, ApproverRole, RemediationStatus
from remediation_controller.domain.proposal import RcaRecommendedActionInput
from remediation_controller.executor.simulation import LocalSimulationExecutor, SimulationState
from remediation_controller.kafka.publisher import RemediationEventPublisher
from remediation_controller.metrics import get_metrics
from remediation_controller.recovery.config import RecoveryVerificationConfig
from remediation_controller.repository import InMemoryRemediationRepository
from remediation_controller.service import RemediationService
from sentinelops_common.contracts import RemediationLifecycleV1
from tests.remediation_controller.kafka_fakes import FakeKafkaProducer

_NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=UTC)
_INCIDENT = "inc_00112233aabbccdd"
_FAST = RecoveryVerificationConfig(timeout_seconds=4, poll_interval_seconds=1.0)
pytestmark = pytest.mark.asyncio


def _service(
    producer: FakeKafkaProducer, *, state: SimulationState | None = None
) -> RemediationService:
    publisher = RemediationEventPublisher(
        producer, topic="remediation.events", source=SERVICE_NAME, metrics=get_metrics()
    )
    executor = LocalSimulationExecutor(state) if state is not None else None
    return RemediationService(
        repository=InMemoryRemediationRepository(),
        executor=executor,
        verify_config=_FAST,
        event_publisher=publisher,
    )


async def _propose(svc: RemediationService, **kw: object) -> str:
    rec = await svc.propose(
        incident_id=_INCIDENT,
        recommendation=RcaRecommendedActionInput(
            action_type="RESTART_SERVICE", target_service="orders-service"
        ),
        incident_severity="HIGH",
        now=_NOW,
        correlation_id="req-1",
        **kw,  # type: ignore[arg-type]
    )
    return rec.remediation_id


async def test_full_happy_path_emits_ordered_events() -> None:
    producer = FakeKafkaProducer()
    svc = _service(producer)
    rid = await _propose(svc)

    await svc.decide(
        rid,
        decision=ApprovalDecision.APPROVE,
        approver_identity="alice@example.com",
        approver_role=ApproverRole.ADMINISTRATOR,
        now=_NOW,
    )
    await svc.execute(rid, now=_NOW)
    out = await svc.verify_recovery(rid, now=_NOW)
    assert out.record.status is RemediationStatus.RECOVERED

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
    # every payload validates against the versioned contract
    for msg in producer.messages:
        RemediationLifecycleV1.model_validate(msg.envelope.payload)


async def test_rejection_path() -> None:
    producer = FakeKafkaProducer()
    svc = _service(producer)
    rid = await _propose(svc)
    await svc.decide(
        rid,
        decision=ApprovalDecision.REJECT,
        approver_identity="bob@example.com",
        approver_role=ApproverRole.OPERATOR,
        now=_NOW,
    )
    assert producer.event_types() == [
        "remediation.proposed",
        "remediation.policy_evaluated",
        "remediation.rejected",
    ]


async def test_blocked_proposal_emits_blocked_event() -> None:
    producer = FakeKafkaProducer()
    svc = _service(producer)
    # mappable action, but severity unknown -> policy severity rule fails closed
    # -> DENY -> persisted BLOCKED (a first-class lifecycle fact).
    rec = await svc.propose(
        incident_id=_INCIDENT,
        recommendation=RcaRecommendedActionInput(
            action_type="RESTART_SERVICE", target_service="orders-service"
        ),
        incident_severity=None,
        now=_NOW,
    )
    assert rec.status is RemediationStatus.BLOCKED
    assert producer.event_types() == [
        "remediation.proposed",
        "remediation.policy_evaluated",
        "remediation.blocked",
    ]


async def test_recovery_failed_path() -> None:
    producer = FakeKafkaProducer()
    state = SimulationState()
    state.inject_fault("orders-service", chronic=True)
    svc = _service(producer, state=state)
    rid = await _propose(svc)
    await svc.decide(
        rid,
        decision=ApprovalDecision.APPROVE,
        approver_identity="alice@example.com",
        approver_role=ApproverRole.ADMINISTRATOR,
        now=_NOW,
    )
    await svc.execute(rid, now=_NOW)
    out = await svc.verify_recovery(rid, now=_NOW)
    assert out.record.status is RemediationStatus.RECOVERY_FAILED
    assert producer.event_types()[-1] == "remediation.recovery_failed"


async def test_dry_run_emits_no_events() -> None:
    producer = FakeKafkaProducer()
    svc = _service(producer)
    rid = await _propose(svc)
    await svc.decide(
        rid,
        decision=ApprovalDecision.APPROVE,
        approver_identity="alice@example.com",
        approver_role=ApproverRole.ADMINISTRATOR,
        now=_NOW,
    )
    before = list(producer.event_types())
    await svc.execute(rid, dry_run=True, now=_NOW)
    assert producer.event_types() == before  # dry-run persists + publishes nothing


async def test_publish_failure_does_not_break_the_transition() -> None:
    producer = FakeKafkaProducer(fail=True)
    svc = _service(producer)
    rid = await _propose(svc)  # publish fails internally, propose still succeeds
    rec = await svc.get(rid)
    assert rec is not None and rec.status is RemediationStatus.PENDING_APPROVAL
    # audit trail is intact even though Kafka got nothing
    events = await svc.list_audit_events(rid)
    assert events is not None and len(events) == 2
    assert producer.messages == []
