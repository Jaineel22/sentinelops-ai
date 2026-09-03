"""RemediationService.execute — authorization, transitions, failure, idempotency (5D)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from remediation_controller.domain import (
    ApprovalDecision,
    ApproverRole,
    RemediationActionType,
    RemediationStatus,
)
from remediation_controller.domain.enums import ExecutionStatus, ExecutorType
from remediation_controller.domain.errors import ApprovalError
from remediation_controller.domain.proposal import RcaRecommendedActionInput
from remediation_controller.executor import ExecutorError
from remediation_controller.executor.base import ExecutionResult
from remediation_controller.repository import (
    InMemoryRemediationRepository,
    InvalidRemediationStateError,
    RemediationExecutionConflictError,
    RemediationExpiredError,
    RemediationNotFoundError,
)
from remediation_controller.service import RemediationService
from tests.remediation_controller.persistence_fakes import BASE_TIME, make_approved_record

_INCIDENT = "inc_00112233aabbccdd"


class _BoomExecutor:
    executor_type = ExecutorType.LOCAL_SIMULATION

    def execute(self, proposal, *, execution_id, dry_run, now):  # type: ignore[no-untyped-def]
        raise ExecutorError("simulated infrastructure hiccup")


async def _approved_via_service(
    svc: RemediationService, *, role: ApproverRole = ApproverRole.INCIDENT_RESPONDER
) -> str:
    rec = await svc.propose(
        incident_id=_INCIDENT,
        recommendation=RcaRecommendedActionInput(
            action_type="RESTART_SERVICE", target_service="orders-service"
        ),
        incident_severity="HIGH",
        now=BASE_TIME,
    )
    await svc.decide(
        rec.remediation_id,
        decision=ApprovalDecision.APPROVE,
        approver_identity="alice",
        approver_role=role,
        now=BASE_TIME + timedelta(minutes=1),
    )
    return rec.remediation_id


# --- happy path -------------------------------------------------
async def test_approved_remediation_executes_to_executed() -> None:
    repo = InMemoryRemediationRepository()
    svc = RemediationService(repository=repo)
    rid = await _approved_via_service(svc)

    outcome = await svc.execute(rid, now=BASE_TIME + timedelta(minutes=2))
    assert outcome.record.status is RemediationStatus.EXECUTED
    assert outcome.result.status is ExecutionStatus.SUCCEEDED
    assert outcome.result.dry_run is False
    assert "restart" in outcome.result.simulated_effect

    reread = await repo.get(rid)
    assert reread is not None
    assert reread.status is RemediationStatus.EXECUTED
    assert reread.execution is not None and reread.execution.status is ExecutionStatus.SUCCEEDED


# --- authorization guards -------------------------------------
async def test_unapproved_remediation_cannot_execute() -> None:
    repo = InMemoryRemediationRepository()
    svc = RemediationService(repository=repo)
    rec = await svc.propose(
        incident_id=_INCIDENT,
        recommendation=RcaRecommendedActionInput(
            action_type="RESTART_SERVICE", target_service="orders-service"
        ),
        incident_severity="HIGH",
        now=BASE_TIME,
    )
    assert rec.status is RemediationStatus.PENDING_APPROVAL
    with pytest.raises(InvalidRemediationStateError):
        await svc.execute(rec.remediation_id, now=BASE_TIME)


@pytest.mark.parametrize("status", [RemediationStatus.REJECTED, RemediationStatus.BLOCKED])
async def test_rejected_or_blocked_cannot_execute(status: RemediationStatus) -> None:
    repo = InMemoryRemediationRepository()
    svc = RemediationService(repository=repo)
    rec = make_approved_record(status=status)
    await repo.create(rec)
    with pytest.raises(InvalidRemediationStateError):
        await svc.execute(rec.remediation_id, now=BASE_TIME)


async def test_missing_approval_cannot_execute() -> None:
    repo = InMemoryRemediationRepository()
    svc = RemediationService(repository=repo)
    # APPROVED status but no approval record (should be impossible in practice)
    rec = make_approved_record()
    rec = rec.model_copy(update={"approval": None})
    await repo.create(rec)
    with pytest.raises(ApprovalError):
        await svc.execute(rec.remediation_id, now=BASE_TIME)


async def test_mismatched_approval_cannot_execute() -> None:
    repo = InMemoryRemediationRepository()
    svc = RemediationService(repository=repo)
    rec = make_approved_record()
    other = rec.approval.model_copy(update={"decision": ApprovalDecision.REJECT})  # type: ignore[union-attr]
    rec = rec.model_copy(update={"approval": other})
    await repo.create(rec)
    with pytest.raises(ApprovalError):
        await svc.execute(rec.remediation_id, now=BASE_TIME)


async def test_expired_remediation_cannot_execute() -> None:
    repo = InMemoryRemediationRepository()
    svc = RemediationService(repository=repo)
    rec = make_approved_record(expires_in=timedelta(minutes=10))
    await repo.create(rec)
    with pytest.raises(RemediationExpiredError):
        await svc.execute(rec.remediation_id, now=BASE_TIME + timedelta(hours=3))


async def test_unknown_remediation_cannot_execute() -> None:
    svc = RemediationService(repository=InMemoryRemediationRepository())
    with pytest.raises(RemediationNotFoundError):
        await svc.execute("rem_ffffffffffffffff")


# --- state-transition safety --------------------------------
async def test_no_shortcut_pending_approval_to_executed() -> None:
    repo = InMemoryRemediationRepository()
    svc = RemediationService(repository=repo)
    rid = await _approved_via_service(svc)
    # tamper: force back to PENDING_APPROVAL
    rec = await repo.get(rid)
    assert rec is not None
    repo._store.records[rid] = rec.with_status(RemediationStatus.PENDING_APPROVAL)
    with pytest.raises(InvalidRemediationStateError):
        await svc.execute(rid, now=BASE_TIME + timedelta(minutes=2))


# --- failure semantics -------------------------------------
async def test_executor_failure_lands_in_execution_failed_not_executed() -> None:
    repo = InMemoryRemediationRepository()
    svc = RemediationService(repository=repo, executor=_BoomExecutor())
    rid = await _approved_via_service(svc)

    outcome = await svc.execute(rid, now=BASE_TIME + timedelta(minutes=2))
    assert outcome.record.status is RemediationStatus.EXECUTION_FAILED  # never EXECUTED
    assert outcome.result.status is ExecutionStatus.FAILED
    assert outcome.result.error is not None and "hiccup" in outcome.result.error

    reread = await repo.get(rid)
    assert reread is not None and reread.status is RemediationStatus.EXECUTION_FAILED


# --- idempotency / double execution -----------------------
async def test_executed_remediation_cannot_execute_again() -> None:
    repo = InMemoryRemediationRepository()
    svc = RemediationService(repository=repo)
    rid = await _approved_via_service(svc)
    await svc.execute(rid, now=BASE_TIME + timedelta(minutes=2))
    with pytest.raises((InvalidRemediationStateError, RemediationExecutionConflictError)):
        await svc.execute(rid, now=BASE_TIME + timedelta(minutes=3))


async def test_execution_failed_remediation_cannot_execute_again() -> None:
    repo = InMemoryRemediationRepository()
    svc = RemediationService(repository=repo, executor=_BoomExecutor())
    rid = await _approved_via_service(svc)
    await svc.execute(rid, now=BASE_TIME + timedelta(minutes=2))
    with pytest.raises((InvalidRemediationStateError, RemediationExecutionConflictError)):
        await svc.execute(rid, now=BASE_TIME + timedelta(minutes=3))


async def test_begin_execution_rejects_a_non_approved_row() -> None:
    repo = InMemoryRemediationRepository()
    rec = make_approved_record(status=RemediationStatus.EXECUTING)
    await repo.create(rec)
    pending = ExecutionResult(
        execution_id="exec_00112233aabbccdd",
        remediation_id=rec.remediation_id,
        action_type=RemediationActionType.RESTART_SERVICE,
        target_service="orders-service",
        target_environment="development",
        executor_type=ExecutorType.LOCAL_SIMULATION,
        status=ExecutionStatus.STARTED,
        dry_run=False,
        started_at=BASE_TIME,
    )
    with pytest.raises(RemediationExecutionConflictError):
        await repo.begin_execution(
            rec.remediation_id, execution_id=pending.execution_id, pending=pending
        )


# --- dry run does not change lifecycle -------------------
async def test_dry_run_does_not_transition_or_persist_execution() -> None:
    repo = InMemoryRemediationRepository()
    svc = RemediationService(repository=repo)
    rid = await _approved_via_service(svc)

    outcome = await svc.execute(rid, dry_run=True, now=BASE_TIME + timedelta(minutes=2))
    assert outcome.result.dry_run is True
    assert outcome.result.status is ExecutionStatus.SUCCEEDED

    reread = await repo.get(rid)
    assert reread is not None
    assert reread.status is RemediationStatus.APPROVED  # unchanged
    assert reread.execution is None  # nothing persisted
    # the real remediation can still be executed afterwards
    real = await svc.execute(rid, now=BASE_TIME + timedelta(minutes=3))
    assert real.record.status is RemediationStatus.EXECUTED


async def test_dry_run_still_requires_approval() -> None:
    repo = InMemoryRemediationRepository()
    svc = RemediationService(repository=repo)
    rec = await svc.propose(
        incident_id=_INCIDENT,
        recommendation=RcaRecommendedActionInput(
            action_type="RESTART_SERVICE", target_service="orders-service"
        ),
        incident_severity="HIGH",
        now=BASE_TIME,
    )
    with pytest.raises(InvalidRemediationStateError):
        await svc.execute(rec.remediation_id, dry_run=True, now=BASE_TIME)


async def test_dry_run_of_a_rolled_back_deployment_does_not_touch_state() -> None:
    repo = InMemoryRemediationRepository()
    svc = RemediationService(repository=repo)
    rec = make_approved_record(action_type=RemediationActionType.ROLL_BACK_DEPLOYMENT)
    await repo.create(rec)
    outcome = await svc.execute(rec.remediation_id, dry_run=True, now=BASE_TIME)
    assert outcome.result.simulated_effect.startswith("[DRY RUN]")
    assert (await repo.get(rec.remediation_id)).status is RemediationStatus.APPROVED  # type: ignore[union-attr]
