"""RemediationService.verify_recovery — lifecycle, transitions, idempotency, audit (5F)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from remediation_controller.audit.model import ActorType, AuditEventType, RemediationAuditEvent
from remediation_controller.domain import ApprovalDecision, ApproverRole, RemediationStatus
from remediation_controller.domain.proposal import RcaRecommendedActionInput
from remediation_controller.executor.simulation import LocalSimulationExecutor, SimulationState
from remediation_controller.recovery.config import RecoveryVerificationConfig
from remediation_controller.recovery.model import VerificationStatus
from remediation_controller.repository import (
    InMemoryRemediationRepository,
    InvalidRemediationStateError,
    RecoveryVerificationConflictError,
    RemediationNotFoundError,
)
from remediation_controller.service import RemediationService
from tests.remediation_controller.policy_fakes import BASE_TIME

_INCIDENT = "inc_00112233aabbccdd"
_T0 = BASE_TIME
_CFG = RecoveryVerificationConfig(timeout_seconds=10, poll_interval_seconds=1.0)


def _service(
    executor: LocalSimulationExecutor | None = None,
) -> tuple[RemediationService, InMemoryRemediationRepository]:
    repo = InMemoryRemediationRepository()
    svc = RemediationService(
        repository=repo,
        executor=executor or LocalSimulationExecutor(),
        verify_config=_CFG,
    )
    return svc, repo


async def _execute(svc: RemediationService) -> str:
    rec = await svc.propose(
        incident_id=_INCIDENT,
        recommendation=RcaRecommendedActionInput(
            action_type="RESTART_SERVICE", target_service="orders-service"
        ),
        incident_severity="HIGH",
        now=_T0,
    )
    await svc.decide(
        rec.remediation_id,
        decision=ApprovalDecision.APPROVE,
        approver_identity="alice",
        approver_role=ApproverRole.ADMINISTRATOR,
        now=_T0 + timedelta(minutes=1),
    )
    out = await svc.execute(rec.remediation_id, now=_T0 + timedelta(minutes=2))
    assert out.record.status is RemediationStatus.EXECUTED
    return rec.remediation_id


async def _events(svc: RemediationService, rid: str) -> list[RemediationAuditEvent]:
    got = await svc.list_audit_events(rid)
    assert got is not None
    return list(got)


# --- happy path: system recovered -----------------------------
async def test_successful_recovery_transitions_to_recovered() -> None:
    svc, repo = _service()
    rid = await _execute(svc)

    outcome = await svc.verify_recovery(rid, now=_T0 + timedelta(minutes=3))
    assert outcome.replayed is False
    assert outcome.record.status is RemediationStatus.RECOVERED
    assert outcome.result.status is VerificationStatus.RECOVERED
    assert outcome.result.attempts >= 1
    assert outcome.result.checks and all(c.passed for c in outcome.result.checks)
    assert outcome.result.failure_reason is None
    assert outcome.result.verifier_type == "DETERMINISTIC_LOCAL"

    reread = await repo.get(rid)
    assert reread is not None
    assert reread.status is RemediationStatus.RECOVERED
    assert reread.verification is not None
    assert reread.verification.status is VerificationStatus.RECOVERED


async def test_successful_recovery_writes_audit_events() -> None:
    svc, _ = _service()
    rid = await _execute(svc)
    await svc.verify_recovery(rid, now=_T0 + timedelta(minutes=3))

    types = [e.event_type for e in await _events(svc, rid)]
    assert types == [
        AuditEventType.PROPOSAL_CREATED,
        AuditEventType.POLICY_EVALUATED,
        AuditEventType.APPROVED,
        AuditEventType.EXECUTION_REQUESTED,
        AuditEventType.EXECUTION_STARTED,
        AuditEventType.EXECUTION_SUCCEEDED,
        AuditEventType.VERIFICATION_STARTED,
        AuditEventType.VERIFICATION_SUCCEEDED,
    ]
    events = await _events(svc, rid)
    started, succeeded = events[-2], events[-1]
    assert started.actor_type is ActorType.SYSTEM
    assert started.previous_state is RemediationStatus.EXECUTED
    assert started.new_state is RemediationStatus.VERIFYING
    assert succeeded.previous_state is RemediationStatus.VERIFYING
    assert succeeded.new_state is RemediationStatus.RECOVERED
    assert started.verification_id is not None
    assert succeeded.verification_id == started.verification_id


# --- failure path: system did NOT recover --------------------
async def test_failed_recovery_transitions_to_recovery_failed() -> None:
    state = SimulationState()
    state.inject_fault("orders-service", chronic=True)  # remediation won't fix it
    svc, repo = _service(LocalSimulationExecutor(state))
    rid = await _execute(svc)

    outcome = await svc.verify_recovery(rid, now=_T0 + timedelta(minutes=3))
    assert outcome.record.status is RemediationStatus.RECOVERY_FAILED
    assert outcome.result.status is VerificationStatus.RECOVERY_FAILED
    assert outcome.result.failure_reason is not None
    assert not all(c.passed for c in outcome.result.checks)

    reread = await repo.get(rid)
    assert reread is not None and reread.status is RemediationStatus.RECOVERY_FAILED
    assert reread.verification is not None


async def test_failed_recovery_writes_verification_failed_audit_event() -> None:
    state = SimulationState()
    state.inject_fault("orders-service", chronic=True)
    svc, _ = _service(LocalSimulationExecutor(state))
    rid = await _execute(svc)
    await svc.verify_recovery(rid, now=_T0 + timedelta(minutes=3))

    events = await _events(svc, rid)
    assert events[-1].event_type is AuditEventType.VERIFICATION_FAILED
    assert AuditEventType.VERIFICATION_SUCCEEDED not in [e.event_type for e in events]
    assert events[-1].new_state is RemediationStatus.RECOVERY_FAILED


async def test_slow_recovery_within_window_still_recovers() -> None:
    state = SimulationState()
    state.inject_fault("orders-service", recover_after=timedelta(seconds=5))
    svc, _ = _service(LocalSimulationExecutor(state))
    rid = await _execute(svc)
    # verify right when execution completed, so the recovery window is still open
    outcome = await svc.verify_recovery(rid, now=_T0 + timedelta(minutes=2))
    assert outcome.record.status is RemediationStatus.RECOVERED
    assert outcome.result.attempts >= 3  # needed several polls


# --- invalid states -----------------------------------------
async def test_cannot_verify_a_pending_remediation() -> None:
    svc, _ = _service()
    rec = await svc.propose(
        incident_id=_INCIDENT,
        recommendation=RcaRecommendedActionInput(
            action_type="RESTART_SERVICE", target_service="orders-service"
        ),
        incident_severity="HIGH",
        now=_T0,
    )
    with pytest.raises(InvalidRemediationStateError):
        await svc.verify_recovery(rec.remediation_id, now=_T0)


async def test_cannot_verify_an_approved_but_unexecuted_remediation() -> None:
    svc, _ = _service()
    rec = await svc.propose(
        incident_id=_INCIDENT,
        recommendation=RcaRecommendedActionInput(
            action_type="RESTART_SERVICE", target_service="orders-service"
        ),
        incident_severity="HIGH",
        now=_T0,
    )
    await svc.decide(
        rec.remediation_id,
        decision=ApprovalDecision.APPROVE,
        approver_identity="alice",
        approver_role=ApproverRole.ADMINISTRATOR,
        now=_T0 + timedelta(minutes=1),
    )
    with pytest.raises(InvalidRemediationStateError):
        await svc.verify_recovery(rec.remediation_id, now=_T0 + timedelta(minutes=2))


async def test_verify_unknown_remediation_raises_not_found() -> None:
    svc, _ = _service()
    with pytest.raises(RemediationNotFoundError):
        await svc.verify_recovery("rem_ffffffffffffffff")


# --- idempotency / retry ------------------------------------
async def test_repeated_verification_replays_the_stored_result() -> None:
    svc, repo = _service()
    rid = await _execute(svc)
    first = await svc.verify_recovery(rid, now=_T0 + timedelta(minutes=3))
    assert first.replayed is False

    second = await svc.verify_recovery(rid, now=_T0 + timedelta(minutes=9))
    assert second.replayed is True
    assert second.result.verification_id == first.result.verification_id
    assert second.record.status is first.record.status

    # no duplicate audit events, no extra verification rows
    types = [e.event_type for e in await _events(svc, rid)]
    assert types.count(AuditEventType.VERIFICATION_STARTED) == 1
    assert types.count(AuditEventType.VERIFICATION_SUCCEEDED) == 1
    assert len(repo._store.verifications) == 1


async def test_repeated_verification_does_not_re_execute_remediation() -> None:
    state = SimulationState()
    svc, _ = _service(LocalSimulationExecutor(state))
    rid = await _execute(svc)
    restarts_after_exec = state.service("orders-service").restart_count
    await svc.verify_recovery(rid, now=_T0 + timedelta(minutes=3))
    await svc.verify_recovery(rid, now=_T0 + timedelta(minutes=4))
    assert state.service("orders-service").restart_count == restarts_after_exec  # unchanged


async def test_verification_while_verifying_conflicts() -> None:
    """A second begin_verification on a VERIFYING remediation is rejected."""

    svc, repo = _service()
    rid = await _execute(svc)
    # tamper the record into VERIFYING with a verification already recorded
    rec = await repo.get(rid)
    assert rec is not None
    repo._store.records[rid] = rec.with_status(RemediationStatus.VERIFYING)
    from remediation_controller.recovery.model import VerificationResult, new_verification_id

    repo._store.verifications[rid] = VerificationResult(
        verification_id=new_verification_id(),
        remediation_id=rid,
        execution_id=rec.execution.execution_id,  # type: ignore[union-attr]
        status=VerificationStatus.STARTED,
        verifier_type="DETERMINISTIC_LOCAL",
        verifier_version="1",
        attempts=0,
        timeout_seconds=10,
        poll_interval_seconds=1.0,
        started_at=_T0,
    )
    with pytest.raises(RecoveryVerificationConflictError):
        await svc.verify_recovery(rid, now=_T0 + timedelta(minutes=3))


# --- no state-machine shortcut ------------------------------
async def test_approved_remediation_cannot_shortcut_to_recovered() -> None:
    svc, repo = _service()
    rec = await svc.propose(
        incident_id=_INCIDENT,
        recommendation=RcaRecommendedActionInput(
            action_type="RESTART_SERVICE", target_service="orders-service"
        ),
        incident_severity="HIGH",
        now=_T0,
    )
    await svc.decide(
        rec.remediation_id,
        decision=ApprovalDecision.APPROVE,
        approver_identity="alice",
        approver_role=ApproverRole.ADMINISTRATOR,
        now=_T0 + timedelta(minutes=1),
    )
    # even if the record is forced to APPROVED, verify refuses (needs EXECUTED)
    with pytest.raises(InvalidRemediationStateError):
        await svc.verify_recovery(rec.remediation_id, now=_T0 + timedelta(minutes=2))
    reread = await repo.get(rec.remediation_id)
    assert reread is not None and reread.status is RemediationStatus.APPROVED
