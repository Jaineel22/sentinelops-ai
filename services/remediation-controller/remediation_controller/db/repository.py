"""PostgreSQL-backed :class:`~remediation_controller.repository.RemediationRepository`.

``record_decision`` holds a ``SELECT ... FOR UPDATE`` row lock and re-checks
"already decided" inside the transaction, so two concurrent approve/reject
requests cannot both win — exactly one becomes the immutable
``remediation_approvals`` row (also guarded by ``UNIQUE(remediation_id)``).

Also implements the synchronous
:class:`~remediation_controller.policy.RemediationHistoryPort` indirectly, via
``history_snapshot`` (an async query that returns a frozen sync snapshot).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from remediation_controller.audit.model import (
    ActorType,
    AuditEventType,
    ExecutionMode,
    RemediationAuditEvent,
)
from remediation_controller.db.engine import Database
from remediation_controller.db.models import (
    RemediationApprovalRow,
    RemediationAuditEventRow,
    RemediationExecutionRow,
    RemediationRow,
    RemediationVerificationRow,
)
from remediation_controller.domain.enums import (
    ApprovalDecision,
    ApproverRole,
    ExecutionStatus,
    ExecutorType,
    RemediationActionType,
    RemediationStatus,
    RemediationTrigger,
    RiskLevel,
)
from remediation_controller.domain.models import RemediationApproval, ServiceTarget
from remediation_controller.domain.proposal import RemediationProposal
from remediation_controller.domain.state_machine import validate_transition
from remediation_controller.executor.base import ExecutionResult
from remediation_controller.policy.codes import PolicyOutcome, PolicyReasonCode
from remediation_controller.policy.decision import PolicyDecision
from remediation_controller.recovery.model import (
    RecoveryCheck,
    VerificationResult,
    VerificationStatus,
)
from remediation_controller.repository import (
    RecoveryVerificationConflictError,
    RemediationAlreadyDecidedError,
    RemediationExecutionConflictError,
    RemediationFilter,
    RemediationHistorySnapshot,
    RemediationNotFoundError,
    RemediationRecord,
)

_ACTIVE_SQL = (
    "PROPOSED",
    "POLICY_EVALUATION",
    "PENDING_APPROVAL",
    "APPROVED",
    "EXECUTING",
    "EXECUTED",
    "VERIFYING",
)
_COOLDOWN_ANCHOR_SQL = ("APPROVED", "EXECUTING", "EXECUTED", "EXECUTION_FAILED")


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _utc_opt(dt: datetime | None) -> datetime | None:
    return _utc(dt) if dt is not None else None


def _row_to_record(row: RemediationRow) -> RemediationRecord:
    proposal = RemediationProposal(
        remediation_id=row.id,
        incident_id=row.incident_id,
        investigation_id=row.investigation_id,
        trigger=RemediationTrigger(row.trigger),
        proposed_by=row.proposed_by,
        action_type=RemediationActionType(row.action_type),
        target=ServiceTarget(service_name=row.target_service, environment=row.target_environment),
        parameters=dict(row.parameters),
        risk_level=RiskLevel(row.risk_level),
        source_recommendation=row.source_recommendation,
        reason=row.reason,
        expected_effect=row.expected_effect,
        evidence_references=tuple(row.evidence_references),
        status=RemediationStatus(row.status),
        created_at=_utc(row.created_at),
        expires_at=_utc(row.expires_at),
    )
    decision = PolicyDecision.model_validate(row.policy_decision)
    approval: RemediationApproval | None = None
    if row.approval is not None:
        a = row.approval
        approval = RemediationApproval(
            approval_id=a.id,
            remediation_id=a.remediation_id,
            decision=ApprovalDecision(a.decision),
            approver_identity=a.approver_identity,
            approver_role=ApproverRole(a.approver_role),
            reason=a.reason,
            decided_at=_utc(a.decided_at),
        )
    execution = _row_to_execution(row.execution) if row.execution is not None else None
    verification = _row_to_verification(row.verification) if row.verification is not None else None
    return RemediationRecord(
        proposal=proposal,
        policy_decision=decision,
        approval=approval,
        execution=execution,
        verification=verification,
    )


def _row_to_execution(row: RemediationExecutionRow) -> ExecutionResult:
    md = {k: v for k, v in dict(row.exec_metadata).items() if isinstance(v, str | int | bool)}
    return ExecutionResult(
        execution_id=row.id,
        remediation_id=row.remediation_id,
        action_type=RemediationActionType(row.action_type),
        target_service=row.target_service,
        target_environment=row.target_environment,
        executor_type=ExecutorType(row.executor_type),
        status=ExecutionStatus(row.status),
        dry_run=row.dry_run,
        started_at=_utc(row.started_at),
        completed_at=_utc_opt(row.completed_at),
        simulated_effect=row.simulated_effect,
        metadata=md,
        error=row.error,
    )


def _execution_to_row(result: ExecutionResult) -> RemediationExecutionRow:
    return RemediationExecutionRow(
        id=result.execution_id,
        remediation_id=result.remediation_id,
        action_type=str(result.action_type),
        target_service=result.target_service,
        target_environment=result.target_environment,
        executor_type=str(result.executor_type),
        status=str(result.status),
        dry_run=result.dry_run,
        simulated_effect=result.simulated_effect,
        exec_metadata=dict(result.metadata),
        error=result.error,
        started_at=result.started_at,
        completed_at=result.completed_at,
    )


def _row_to_verification(row: RemediationVerificationRow) -> VerificationResult:
    checks = tuple(RecoveryCheck.model_validate(c) for c in row.checks if isinstance(c, dict))
    md = {k: v for k, v in dict(row.ver_metadata).items() if isinstance(v, str | int | bool)}
    return VerificationResult(
        verification_id=row.id,
        remediation_id=row.remediation_id,
        execution_id=row.execution_id,
        status=VerificationStatus(row.status),
        verifier_type=row.verifier_type,
        verifier_version=row.verifier_version,
        attempts=row.attempts,
        checks=checks,
        failure_reason=row.failure_reason,
        timeout_seconds=row.timeout_seconds,
        poll_interval_seconds=row.poll_interval_seconds,
        metadata=md,
        started_at=_utc(row.started_at),
        completed_at=_utc_opt(row.completed_at),
    )


def _verification_to_row(result: VerificationResult) -> RemediationVerificationRow:
    return RemediationVerificationRow(
        id=result.verification_id,
        remediation_id=result.remediation_id,
        execution_id=result.execution_id,
        status=str(result.status),
        verifier_type=result.verifier_type,
        verifier_version=result.verifier_version,
        attempts=result.attempts,
        checks=[c.model_dump(mode="json") for c in result.checks],
        failure_reason=result.failure_reason,
        timeout_seconds=result.timeout_seconds,
        poll_interval_seconds=result.poll_interval_seconds,
        ver_metadata=dict(result.metadata),
        started_at=result.started_at,
        completed_at=result.completed_at,
    )


def _audit_to_row(event: RemediationAuditEvent) -> RemediationAuditEventRow:
    return RemediationAuditEventRow(
        audit_id=event.audit_id,
        remediation_id=event.remediation_id,
        incident_id=event.incident_id,
        investigation_id=event.investigation_id,
        event_type=str(event.event_type),
        actor_type=str(event.actor_type),
        actor_id=event.actor_id,
        actor_role=str(event.actor_role) if event.actor_role is not None else None,
        previous_state=str(event.previous_state) if event.previous_state is not None else None,
        new_state=str(event.new_state) if event.new_state is not None else None,
        action_type=str(event.action_type) if event.action_type is not None else None,
        target_service=event.target_service,
        target_environment=event.target_environment,
        policy_outcome=str(event.policy_outcome) if event.policy_outcome is not None else None,
        policy_version=event.policy_version,
        policy_reason_codes=[str(c) for c in event.policy_reason_codes],
        execution_id=event.execution_id,
        execution_mode=str(event.execution_mode) if event.execution_mode is not None else None,
        execution_result=(
            str(event.execution_result) if event.execution_result is not None else None
        ),
        verification_id=event.verification_id,
        reason=event.reason,
        correlation_id=event.correlation_id,
        event_metadata=dict(event.metadata),
        occurred_at=event.occurred_at,
        recorded_at=datetime.now(tz=UTC),
    )


def _row_to_audit(row: RemediationAuditEventRow) -> RemediationAuditEvent:
    md = {k: v for k, v in dict(row.event_metadata).items() if isinstance(v, str | int | bool)}
    return RemediationAuditEvent(
        audit_id=row.audit_id,
        remediation_id=row.remediation_id,
        incident_id=row.incident_id,
        investigation_id=row.investigation_id,
        event_type=AuditEventType(row.event_type),
        actor_type=ActorType(row.actor_type),
        actor_id=row.actor_id,
        actor_role=ApproverRole(row.actor_role) if row.actor_role else None,
        previous_state=RemediationStatus(row.previous_state) if row.previous_state else None,
        new_state=RemediationStatus(row.new_state) if row.new_state else None,
        action_type=RemediationActionType(row.action_type) if row.action_type else None,
        target_service=row.target_service,
        target_environment=row.target_environment,
        policy_outcome=PolicyOutcome(row.policy_outcome) if row.policy_outcome else None,
        policy_version=row.policy_version,
        policy_reason_codes=tuple(PolicyReasonCode(c) for c in row.policy_reason_codes),
        execution_id=row.execution_id,
        execution_mode=ExecutionMode(row.execution_mode) if row.execution_mode else None,
        execution_result=ExecutionStatus(row.execution_result) if row.execution_result else None,
        verification_id=row.verification_id,
        reason=row.reason,
        correlation_id=row.correlation_id,
        metadata=md,
        occurred_at=_utc(row.occurred_at),
    )


def _record_to_row(record: RemediationRecord) -> RemediationRow:
    p = record.proposal
    d = record.policy_decision
    now = datetime.now(tz=UTC)
    return RemediationRow(
        id=p.remediation_id,
        incident_id=p.incident_id,
        investigation_id=p.investigation_id,
        trigger=str(p.trigger),
        proposed_by=p.proposed_by,
        action_type=str(p.action_type),
        target_service=p.target.service_name,
        target_environment=p.target.environment,
        parameters=dict(p.parameters),
        risk_level=str(p.risk_level),
        source_recommendation=p.source_recommendation,
        reason=p.reason,
        expected_effect=p.expected_effect,
        evidence_references=list(p.evidence_references),
        status=str(p.status),
        policy_outcome=str(d.outcome),
        policy_version=d.policy_version,
        policy_reason_codes=[str(c) for c in d.reason_codes],
        policy_decision=d.model_dump(mode="json"),
        policy_evaluated_at=d.evaluated_at,
        created_at=p.created_at,
        expires_at=p.expires_at,
        updated_at=now,
        decided_at=record.approval.decided_at if record.approval else None,
    )


class SqlRemediationRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def create(
        self,
        record: RemediationRecord,
        *,
        audit_events: Sequence[RemediationAuditEvent] = (),
    ) -> RemediationRecord:
        async with self._db.session() as session, session.begin():
            session.add(_record_to_row(record))
            # Flush the parent row before the audit rows so the audit FK
            # (remediation_id -> remediations.id) resolves; still one transaction.
            await session.flush()
            for event in audit_events:
                session.add(_audit_to_row(event))
            await session.flush()
        return record

    async def get(self, remediation_id: str) -> RemediationRecord | None:
        async with self._db.session() as session:
            row = await session.scalar(
                select(RemediationRow)
                .options(
                    selectinload(RemediationRow.approval),
                    selectinload(RemediationRow.execution),
                    selectinload(RemediationRow.verification),
                )
                .where(RemediationRow.id == remediation_id)
            )
            return _row_to_record(row) if row is not None else None

    async def list(self, flt: RemediationFilter) -> list[RemediationRecord]:
        stmt = (
            select(RemediationRow)
            .options(
                selectinload(RemediationRow.approval),
                selectinload(RemediationRow.execution),
                selectinload(RemediationRow.verification),
            )
            .order_by(RemediationRow.created_at.desc())
        )
        if flt.incident_id is not None:
            stmt = stmt.where(RemediationRow.incident_id == flt.incident_id)
        if flt.status is not None:
            stmt = stmt.where(RemediationRow.status == str(flt.status))
        if flt.action_type is not None:
            stmt = stmt.where(RemediationRow.action_type == str(flt.action_type))
        if flt.since is not None:
            stmt = stmt.where(RemediationRow.created_at >= flt.since)
        stmt = stmt.limit(flt.limit).offset(flt.offset)
        async with self._db.session() as session:
            rows = (await session.scalars(stmt)).all()
            return [_row_to_record(r) for r in rows]

    async def record_decision(
        self,
        remediation_id: str,
        *,
        new_proposal: RemediationProposal,
        approval: RemediationApproval,
        audit_events: Sequence[RemediationAuditEvent] = (),
    ) -> RemediationRecord:
        now = datetime.now(tz=UTC)
        async with self._db.session() as session, session.begin():
            row = await session.get(RemediationRow, remediation_id, with_for_update=True)
            if row is None:
                raise RemediationNotFoundError(remediation_id)
            existing = await session.scalar(
                select(RemediationApprovalRow).where(
                    RemediationApprovalRow.remediation_id == remediation_id
                )
            )
            if existing is not None:
                raise RemediationAlreadyDecidedError(remediation_id)

            row.status = str(new_proposal.status)
            row.updated_at = now
            row.decided_at = approval.decided_at
            session.add(
                RemediationApprovalRow(
                    id=approval.approval_id,
                    remediation_id=remediation_id,
                    decision=str(approval.decision),
                    approver_identity=approval.approver_identity,
                    approver_role=str(approval.approver_role),
                    reason=approval.reason,
                    decided_at=approval.decided_at,
                    created_at=now,
                )
            )
            for event in audit_events:
                session.add(_audit_to_row(event))
            try:
                await session.flush()
            except IntegrityError as exc:  # concurrent writer won the race
                raise RemediationAlreadyDecidedError(remediation_id) from exc

        got = await self.get(remediation_id)
        assert got is not None
        return got

    async def begin_execution(
        self,
        remediation_id: str,
        *,
        execution_id: str,
        pending: ExecutionResult,
        audit_events: Sequence[RemediationAuditEvent] = (),
    ) -> RemediationRecord:
        now = datetime.now(tz=UTC)
        async with self._db.session() as session, session.begin():
            row = await session.get(RemediationRow, remediation_id, with_for_update=True)
            if row is None:
                raise RemediationNotFoundError(remediation_id)
            if row.status != str(RemediationStatus.APPROVED):
                raise RemediationExecutionConflictError(
                    f"remediation {remediation_id} is {row.status}, not APPROVED"
                )
            validate_transition(RemediationStatus.APPROVED, RemediationStatus.EXECUTING)
            row.status = str(RemediationStatus.EXECUTING)
            row.updated_at = now
            session.add(_execution_to_row(pending))
            for event in audit_events:
                session.add(_audit_to_row(event))
            try:
                await session.flush()
            except IntegrityError as exc:  # UNIQUE(remediation_id) — concurrent execution
                raise RemediationExecutionConflictError(remediation_id) from exc

        got = await self.get(remediation_id)
        assert got is not None
        return got

    async def finish_execution(
        self,
        remediation_id: str,
        *,
        execution_id: str,
        result: ExecutionResult,
        final_status: RemediationStatus,
        audit_events: Sequence[RemediationAuditEvent] = (),
    ) -> RemediationRecord:
        now = datetime.now(tz=UTC)
        async with self._db.session() as session, session.begin():
            row = await session.get(RemediationRow, remediation_id, with_for_update=True)
            if row is None:  # pragma: no cover - caller just began it
                raise RemediationNotFoundError(remediation_id)
            if row.status != str(RemediationStatus.EXECUTING):  # pragma: no cover - defensive
                raise RemediationExecutionConflictError(remediation_id)
            validate_transition(RemediationStatus.EXECUTING, final_status)
            row.status = str(final_status)
            row.updated_at = now
            exec_row = await session.get(RemediationExecutionRow, execution_id)
            if exec_row is None:  # pragma: no cover - inserted in begin_execution
                raise RemediationExecutionConflictError(remediation_id)
            exec_row.status = str(result.status)
            exec_row.simulated_effect = result.simulated_effect
            exec_row.exec_metadata = dict(result.metadata)
            exec_row.error = result.error
            exec_row.completed_at = result.completed_at
            for event in audit_events:
                session.add(_audit_to_row(event))
            await session.flush()

        got = await self.get(remediation_id)
        assert got is not None
        return got

    async def begin_verification(
        self,
        remediation_id: str,
        *,
        verification_id: str,
        pending: VerificationResult,
        audit_events: Sequence[RemediationAuditEvent] = (),
    ) -> RemediationRecord:
        now = datetime.now(tz=UTC)
        async with self._db.session() as session, session.begin():
            row = await session.get(RemediationRow, remediation_id, with_for_update=True)
            if row is None:
                raise RemediationNotFoundError(remediation_id)
            if row.status != str(RemediationStatus.EXECUTED):
                raise RecoveryVerificationConflictError(
                    f"remediation {remediation_id} is {row.status}, not EXECUTED"
                )
            validate_transition(RemediationStatus.EXECUTED, RemediationStatus.VERIFYING)
            row.status = str(RemediationStatus.VERIFYING)
            row.updated_at = now
            session.add(_verification_to_row(pending))
            for event in audit_events:
                session.add(_audit_to_row(event))
            try:
                await session.flush()
            except IntegrityError as exc:  # UNIQUE(remediation_id) — concurrent verification
                raise RecoveryVerificationConflictError(remediation_id) from exc

        got = await self.get(remediation_id)
        assert got is not None
        return got

    async def finish_verification(
        self,
        remediation_id: str,
        *,
        verification_id: str,
        result: VerificationResult,
        final_status: RemediationStatus,
        audit_events: Sequence[RemediationAuditEvent] = (),
    ) -> RemediationRecord:
        now = datetime.now(tz=UTC)
        async with self._db.session() as session, session.begin():
            row = await session.get(RemediationRow, remediation_id, with_for_update=True)
            if row is None:  # pragma: no cover - caller just began it
                raise RemediationNotFoundError(remediation_id)
            if row.status != str(RemediationStatus.VERIFYING):  # pragma: no cover - defensive
                raise RecoveryVerificationConflictError(remediation_id)
            validate_transition(RemediationStatus.VERIFYING, final_status)
            row.status = str(final_status)
            row.updated_at = now
            ver_row = await session.get(RemediationVerificationRow, verification_id)
            if ver_row is None:  # pragma: no cover - inserted in begin_verification
                raise RecoveryVerificationConflictError(remediation_id)
            ver_row.status = str(result.status)
            ver_row.attempts = result.attempts
            ver_row.checks = [c.model_dump(mode="json") for c in result.checks]
            ver_row.failure_reason = result.failure_reason
            ver_row.ver_metadata = dict(result.metadata)
            ver_row.completed_at = result.completed_at
            for event in audit_events:
                session.add(_audit_to_row(event))
            await session.flush()

        got = await self.get(remediation_id)
        assert got is not None
        return got

    async def list_audit_events(
        self,
        remediation_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[RemediationAuditEvent]:
        stmt = (
            select(RemediationAuditEventRow)
            .where(RemediationAuditEventRow.remediation_id == remediation_id)
            .order_by(RemediationAuditEventRow.seq.asc())
            .limit(limit)
            .offset(offset)
        )
        async with self._db.session() as session:
            rows = (await session.scalars(stmt)).all()
            return [_row_to_audit(r) for r in rows]

    async def history_snapshot(
        self,
        *,
        incident_id: str,
        action_type: RemediationActionType,
        target: ServiceTarget,
    ) -> RemediationHistorySnapshot:
        key = (
            RemediationRow.incident_id == incident_id,
            RemediationRow.action_type == str(action_type),
            RemediationRow.target_service == target.service_name,
            RemediationRow.target_environment == target.environment,
        )
        async with self._db.session() as session:
            active = await session.scalar(
                select(func.count())
                .select_from(RemediationRow)
                .where(*key, RemediationRow.status.in_(_ACTIVE_SQL))
            )
            last_completed = await session.scalar(
                select(func.max(RemediationRow.decided_at)).where(
                    *key, RemediationRow.status.in_(_COOLDOWN_ANCHOR_SQL)
                )
            )
            return RemediationHistorySnapshot(
                active_exists=bool(active or 0),
                last_completed=_utc_opt(last_completed),
            )

    async def health_check(self) -> bool:
        return await self._db.ping()


__all__ = ["SqlRemediationRepository"]
