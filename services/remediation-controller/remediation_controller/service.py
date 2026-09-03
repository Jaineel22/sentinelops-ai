"""The remediation orchestration service (Phase 5C).

Ties the 5A domain, the 5B policy engine, and the 5C repository together:

    propose()  proposal_from_rca -> PolicyEngine.evaluate -> apply_policy_decision
               -> persist (status PENDING_APPROVAL or BLOCKED)

    decide()   authorize (deterministic) -> validate lifecycle + expiry
               -> 5A state-machine transition -> persist the immutable approval
               (all under the repository's row lock)

**Phase 5C never executes anything.** ``decide`` can only move a remediation to
``APPROVED`` or ``REJECTED``. ``APPROVED != EXECUTED`` — execution is Phase 5D.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from remediation_controller.audit import (
    RemediationAuditEvent,
    decision_event,
    execution_finished_event,
    execution_requested_event,
    execution_started_event,
    policy_evaluated_event,
    proposal_created_event,
    remediation_blocked_event,
    verification_finished_event,
    verification_started_event,
)
from remediation_controller.authorization import can_approve
from remediation_controller.domain.catalogue import (
    is_allowed_target,
    require_action_definition,
    validate_action_parameters,
)
from remediation_controller.domain.enums import (
    ApprovalDecision,
    ApproverRole,
    ExecutionStatus,
    ExecutorType,
    RemediationStatus,
)
from remediation_controller.domain.errors import RemediationDomainError, UnknownTargetError
from remediation_controller.domain.models import RemediationApproval, new_approval_id
from remediation_controller.domain.proposal import (
    BlockedProposal,
    RcaRecommendedActionInput,
    authorize_execution,
    proposal_from_rca,
)
from remediation_controller.domain.state_machine import validate_transition
from remediation_controller.executor import (
    ExecutionResult,
    Executor,
    ExecutorError,
    build_executor,
    new_execution_id,
)
from remediation_controller.kafka.publisher import RemediationEventPublisher
from remediation_controller.metrics import RemediationMetrics, get_metrics
from remediation_controller.policy import (
    PolicyContext,
    PolicyEngine,
    PolicyOutcome,
    apply_policy_decision,
)
from remediation_controller.recovery import (
    RecoveryVerificationConfig,
    RecoveryVerifier,
    VerificationResult,
    VerificationStatus,
    build_default_verifier,
    new_verification_id,
)
from remediation_controller.repository import (
    InvalidRemediationStateError,
    ProposalNotMappableError,
    RecoveryVerificationConflictError,
    RemediationExpiredError,
    RemediationFilter,
    RemediationNotFoundError,
    RemediationPolicyBlockedError,
    RemediationRecord,
    RemediationRepository,
    UnauthorizedApproverError,
)

logger = logging.getLogger("remediation_controller.service")

_DEFAULT_TTL = timedelta(hours=1)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True)
class ExecutionOutcome:
    """The result of an execute / dry-run call: the (possibly-transitioned)
    remediation record and the structured :class:`ExecutionResult`."""

    record: RemediationRecord
    result: ExecutionResult


@dataclass(frozen=True)
class VerificationOutcome:
    """The result of a verify-recovery call: the (possibly-transitioned)
    remediation record and the structured :class:`VerificationResult`.
    ``replayed`` is ``True`` when the remediation was already ``RECOVERED`` /
    ``RECOVERY_FAILED`` and the stored verification was returned unchanged
    (idempotent retry)."""

    record: RemediationRecord
    result: VerificationResult
    replayed: bool


class RemediationService:
    def __init__(
        self,
        *,
        repository: RemediationRepository,
        policy_engine: PolicyEngine | None = None,
        executor: Executor | None = None,
        proposal_ttl: timedelta = _DEFAULT_TTL,
        metrics: RemediationMetrics | None = None,
        verifier: RecoveryVerifier | None = None,
        verify_config: RecoveryVerificationConfig | None = None,
        event_publisher: RemediationEventPublisher | None = None,
    ) -> None:
        self._repo = repository
        self._policy = policy_engine or PolicyEngine()
        self._executor = executor or build_executor(ExecutorType.LOCAL_SIMULATION)
        self._ttl = proposal_ttl
        self._metrics = metrics or get_metrics()
        # Phase 5G: best-effort lifecycle event publication after each committed
        # transition. ``None`` -> audit-trail-only (Kafka disabled / unavailable).
        self._events = event_publisher
        # The verifier only observes — it holds a read-only health probe over the
        # executor's simulation state, never any execution capability (Phase 5F).
        self._verifier = verifier or build_default_verifier(self._executor)
        self._verify_config = verify_config or RecoveryVerificationConfig()

    async def _commit_with_audit(
        self,
        events: list[RemediationAuditEvent],
        commit: Awaitable[RemediationRecord],
    ) -> RemediationRecord:
        """Await a repository call that persists ``events`` atomically with a
        state change. Record the ``audit.events_written`` metric on success; a
        ``RemediationDomainError`` is expected control flow (already-decided,
        execution conflict, …) and is re-raised untouched, but any other failure
        means a committed-or-nothing transaction rolled back — count it as an
        audit write failure and re-raise."""

        try:
            stored = await commit
        except RemediationDomainError:
            raise
        except Exception:
            self._metrics.record_audit_write_failure()
            raise
        self._metrics.record_audit_events(events)
        # Phase 5G: the transition + its audit rows are now committed. Mirror the
        # committed facts onto Kafka, best-effort — a publish failure never
        # undoes the transition (the audit trail is the durable record).
        if self._events is not None:
            await self._events.publish_audit_events(events)
        return stored

    # --- creation ---------------------------------------------------
    async def propose(
        self,
        *,
        incident_id: str,
        recommendation: RcaRecommendedActionInput,
        investigation_id: str | None = None,
        incident_severity: str | None = None,
        target_environment: str = "development",
        proposed_by: str = "api",
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> RemediationRecord:
        """Map an RCA recommendation, run policy, persist the result.

        Returns a persisted :class:`RemediationRecord` with status
        ``PENDING_APPROVAL`` (policy allowed) or ``BLOCKED`` (policy denied).
        Raises :class:`ProposalNotMappableError` if the recommendation cannot be
        mapped to a closed-catalogue action/target — nothing is persisted.

        Appends the ``PROPOSAL_CREATED`` and ``POLICY_EVALUATED`` audit events
        (plus ``REMEDIATION_BLOCKED`` on a policy denial) atomically with the
        persisted row (Phase 5E).
        """

        ts = now or _utcnow()
        mapped = proposal_from_rca(
            recommendation,
            incident_id=incident_id,
            investigation_id=investigation_id,
            incident_severity=incident_severity,
            target_environment=target_environment,
            proposed_by=proposed_by,
            now=ts,
            ttl=self._ttl,
        )
        if isinstance(mapped, BlockedProposal):
            raise ProposalNotMappableError(mapped.block_reason)

        # proposal_from_rca already stamps trigger=RCA_RECOMMENDATION and a
        # catalogue-derived risk level, and has fully validated the proposal.
        proposal = mapped

        snapshot = await self._repo.history_snapshot(
            incident_id=incident_id,
            action_type=proposal.action_type,
            target=proposal.target,
        )
        context = PolicyContext(now=ts, incident_severity=incident_severity, history=snapshot)
        decision = self._policy.evaluate(proposal, context)
        advanced = apply_policy_decision(proposal, decision)

        record = RemediationRecord(proposal=advanced, policy_decision=decision)

        audit_events = [
            proposal_created_event(proposal, correlation_id=correlation_id, now=ts),
            policy_evaluated_event(
                advanced, decision, new_state=advanced.status, correlation_id=correlation_id, now=ts
            ),
        ]
        if not decision.allowed:
            audit_events.append(
                remediation_blocked_event(advanced, decision, correlation_id=correlation_id, now=ts)
            )
        stored = await self._commit_with_audit(
            audit_events, self._repo.create(record, audit_events=audit_events)
        )
        logger.info(
            "remediation proposed",
            extra={
                "remediation_id": stored.remediation_id,
                "incident_id": incident_id,
                "action_type": str(proposal.action_type),
                "status": str(stored.status),
                "policy_outcome": str(decision.outcome),
            },
        )
        return stored

    # --- reads -----------------------------------------------------
    async def get(self, remediation_id: str) -> RemediationRecord | None:
        return await self._repo.get(remediation_id)

    async def list(self, flt: RemediationFilter) -> list[RemediationRecord]:
        return await self._repo.list(flt)

    async def list_audit_events(
        self, remediation_id: str, *, limit: int = 100, offset: int = 0
    ) -> Sequence[RemediationAuditEvent] | None:
        """The remediation's append-only audit trail, oldest first. Returns
        ``None`` if the remediation does not exist (so the API can answer 404)."""

        if await self._repo.get(remediation_id) is None:
            return None
        return await self._repo.list_audit_events(remediation_id, limit=limit, offset=offset)

    # --- human decision ------------------------------------------
    async def decide(
        self,
        remediation_id: str,
        *,
        decision: ApprovalDecision,
        approver_identity: str,
        approver_role: ApproverRole,
        reason: str = "",
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> RemediationRecord:
        """Record a human APPROVE / REJECT decision.

        Deterministic guards (checked again under the repository's row lock):
        the remediation exists, is ``PENDING_APPROVAL``, has not expired, was not
        policy-blocked, and — for APPROVE — the role is authorized for the
        action's catalogue risk level. Transitions only to ``APPROVED`` /
        ``REJECTED`` via the Phase 5A state machine.

        Appends the ``APPROVED`` / ``REJECTED`` audit event atomically with the
        immutable approval row (Phase 5E).
        """

        ts = now or _utcnow()
        current = await self._repo.get(remediation_id)
        if current is None:
            raise RemediationNotFoundError(remediation_id)

        self._check_decidable(current, decision=decision, approver_role=approver_role, now=ts)

        target_status = (
            RemediationStatus.APPROVED
            if decision is ApprovalDecision.APPROVE
            else RemediationStatus.REJECTED
        )
        validate_transition(current.status, target_status)  # Phase 5A guard

        new_proposal = current.proposal.model_copy(update={"status": target_status})
        approval = RemediationApproval(
            approval_id=new_approval_id(),
            remediation_id=remediation_id,
            decision=decision,
            approver_identity=approver_identity,
            approver_role=approver_role,
            reason=reason,
            decided_at=ts,
        )
        audit_events = [
            decision_event(
                current.proposal,
                approval,
                previous_state=current.status,
                correlation_id=correlation_id,
                now=ts,
            )
        ]
        stored = await self._commit_with_audit(
            audit_events,
            self._repo.record_decision(
                remediation_id,
                new_proposal=new_proposal,
                approval=approval,
                audit_events=audit_events,
            ),
        )
        logger.info(
            "remediation decision recorded",
            extra={
                "remediation_id": remediation_id,
                "decision": str(decision),
                "approver_role": str(approver_role),
                "new_status": str(target_status),
            },
        )
        return stored

    @staticmethod
    def _check_decidable(
        record: RemediationRecord,
        *,
        decision: ApprovalDecision,
        approver_role: ApproverRole,
        now: datetime,
    ) -> None:
        if record.approval is not None:
            raise InvalidRemediationStateError(
                f"remediation {record.remediation_id} already has a {record.approval.decision} "
                f"decision"
            )
        if record.status is not RemediationStatus.PENDING_APPROVAL:
            raise InvalidRemediationStateError(
                f"remediation {record.remediation_id} is {record.status}, not PENDING_APPROVAL"
            )
        if record.policy_decision.outcome is not PolicyOutcome.ALLOW:  # defensive
            raise RemediationPolicyBlockedError(record.remediation_id)
        expires = record.proposal.expires_at
        expires = expires if expires.tzinfo is not None else expires.replace(tzinfo=UTC)
        if now >= expires:
            raise RemediationExpiredError(
                f"remediation {record.remediation_id} expired at {expires.isoformat()}"
            )
        if decision is ApprovalDecision.APPROVE and not can_approve(
            approver_role, record.proposal.action_type
        ):
            raise UnauthorizedApproverError(
                f"role {approver_role} may not approve a {record.proposal.action_type} action"
            )

    # --- execution (Phase 5D) -----------------------------------
    async def execute(
        self,
        remediation_id: str,
        *,
        dry_run: bool = False,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ExecutionOutcome:
        """Execute an APPROVED remediation through the allow-listed executor.

        ``dry_run=True`` runs the same authorization guards and the same executor
        interface but persists nothing, mutates no simulation state, and writes
        **no audit event** — it is a read-only preview (ADR-027).

        A real run: ``APPROVED -> EXECUTING`` (claimed atomically under a row
        lock — the sole edge, idempotent), then the executor, then
        ``EXECUTING -> EXECUTED`` (success) or ``EXECUTING -> EXECUTION_FAILED``
        (the executor raised). A failed execution never becomes ``EXECUTED``.
        Audit events (``EXECUTION_REQUESTED`` + ``EXECUTION_STARTED`` with the
        claim; ``EXECUTION_SUCCEEDED`` / ``EXECUTION_FAILED`` with the terminal
        result) are committed in the same transactions as the state changes.
        """

        ts = now or _utcnow()
        record = await self._repo.get(remediation_id)
        if record is None:
            raise RemediationNotFoundError(remediation_id)
        self._check_executable(record, now=ts)

        execution_id = new_execution_id()
        proposal = record.proposal

        if dry_run:
            result = self._executor.execute(
                proposal, execution_id=execution_id, dry_run=True, now=ts
            )
            logger.info(
                "remediation dry-run",
                extra={
                    "remediation_id": remediation_id,
                    "action_type": str(proposal.action_type),
                    "execution_id": execution_id,
                },
            )
            return ExecutionOutcome(record=record, result=result)

        pending = ExecutionResult(
            execution_id=execution_id,
            remediation_id=remediation_id,
            action_type=proposal.action_type,
            target_service=proposal.target.service_name,
            target_environment=proposal.target.environment,
            executor_type=self._executor.executor_type,
            status=ExecutionStatus.STARTED,
            dry_run=False,
            started_at=ts,
        )
        begin_audit = [
            execution_requested_event(
                proposal, execution_id=execution_id, correlation_id=correlation_id, now=ts
            ),
            execution_started_event(
                proposal, execution_id=execution_id, correlation_id=correlation_id, now=ts
            ),
        ]
        begun = await self._commit_with_audit(
            begin_audit,
            self._repo.begin_execution(
                remediation_id,
                execution_id=execution_id,
                pending=pending,
                audit_events=begin_audit,
            ),
        )

        try:
            result = self._executor.execute(
                begun.proposal, execution_id=execution_id, dry_run=False, now=ts
            )
            final_status = RemediationStatus.EXECUTED
        except ExecutorError as exc:
            result = pending.model_copy(
                update={
                    "status": ExecutionStatus.FAILED,
                    "error": str(exc)[:2000],
                    "completed_at": _utcnow(),
                    "simulated_effect": f"execution failed: {exc}"[:2000],
                }
            )
            final_status = RemediationStatus.EXECUTION_FAILED
            logger.warning(
                "remediation execution failed",
                extra={"remediation_id": remediation_id, "execution_id": execution_id},
            )

        finish_audit = [
            execution_finished_event(
                begun.proposal,
                result,
                final_state=final_status,
                correlation_id=correlation_id,
                now=ts,
            )
        ]
        completed = await self._commit_with_audit(
            finish_audit,
            self._repo.finish_execution(
                remediation_id,
                execution_id=execution_id,
                result=result,
                final_status=final_status,
                audit_events=finish_audit,
            ),
        )
        logger.info(
            "remediation execution complete",
            extra={
                "remediation_id": remediation_id,
                "execution_id": execution_id,
                "new_status": str(final_status),
                "execution_status": str(result.status),
            },
        )
        return ExecutionOutcome(record=completed, result=result)

    @staticmethod
    def _check_executable(record: RemediationRecord, *, now: datetime) -> None:
        """Deterministic pre-lock guards. Re-checked atomically by
        ``repo.begin_execution`` (the ``APPROVED -> EXECUTING`` claim)."""

        if record.status is not RemediationStatus.APPROVED:
            raise InvalidRemediationStateError(
                f"remediation {record.remediation_id} is {record.status}, not APPROVED"
            )
        # The Phase 5A guard: APPROVED + a matching, affirmative, immutable approval.
        authorize_execution(record.proposal, record.approval)

        proposal = record.proposal
        require_action_definition(proposal.action_type)  # closed catalogue
        if not is_allowed_target(proposal.action_type, proposal.target):
            raise UnknownTargetError(
                f"target {proposal.target} is not allow-listed for {proposal.action_type}"
            )
        validate_action_parameters(proposal.action_type, proposal.parameters)

        expires = proposal.expires_at
        expires = expires if expires.tzinfo is not None else expires.replace(tzinfo=UTC)
        if now >= expires:
            raise RemediationExpiredError(
                f"remediation {record.remediation_id} expired at {expires.isoformat()}"
            )

    # --- recovery verification (Phase 5F) ----------------------
    async def verify_recovery(
        self,
        remediation_id: str,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> VerificationOutcome:
        """Verify whether the target system actually recovered after execution.

        ``EXECUTED -> VERIFYING`` (claimed atomically under a row lock, the sole
        edge, idempotent via ``UNIQUE(remediation_id)``), then a bounded,
        deterministic poll-and-evaluate loop against an observable health signal,
        then ``VERIFYING -> RECOVERED`` (every check passed within the window) or
        ``VERIFYING -> RECOVERY_FAILED`` (window exhausted). The verifier only
        **observes** — it never executes anything, never re-runs the remediation,
        and never bypasses approval.

        Safe to retry: once the remediation is ``RECOVERED`` / ``RECOVERY_FAILED``
        the stored verification is returned unchanged (``replayed=True``); a
        second attempt while ``VERIFYING`` conflicts. Audit events
        (``VERIFICATION_STARTED``; ``VERIFICATION_SUCCEEDED`` /
        ``VERIFICATION_FAILED``) are committed in the same transactions as the
        state changes.
        """

        ts = now or _utcnow()
        record = await self._repo.get(remediation_id)
        if record is None:
            raise RemediationNotFoundError(remediation_id)

        if record.status in (RemediationStatus.RECOVERED, RemediationStatus.RECOVERY_FAILED):
            if record.verification is None:  # pragma: no cover - defensive
                raise InvalidRemediationStateError(
                    f"remediation {remediation_id} is {record.status} but has no verification"
                )
            logger.info(
                "recovery verification replayed",
                extra={"remediation_id": remediation_id, "status": str(record.status)},
            )
            return VerificationOutcome(record=record, result=record.verification, replayed=True)

        if record.status is RemediationStatus.VERIFYING:
            raise RecoveryVerificationConflictError(
                f"a recovery verification for {remediation_id} is already in progress"
            )

        self._check_verifiable(record)
        assert record.execution is not None  # guaranteed by _check_verifiable
        execution_id = record.execution.execution_id
        verification_id = new_verification_id()
        cfg = self._verify_config

        pending = VerificationResult(
            verification_id=verification_id,
            remediation_id=remediation_id,
            execution_id=execution_id,
            status=VerificationStatus.STARTED,
            verifier_type=self._verifier.verifier_type,
            verifier_version=self._verifier.verifier_version,
            attempts=0,
            checks=(),
            failure_reason=None,
            timeout_seconds=cfg.timeout_seconds,
            poll_interval_seconds=cfg.poll_interval_seconds,
            metadata={},
            started_at=ts,
            completed_at=None,
        )
        begin_audit = [
            verification_started_event(
                record.proposal,
                verification_id=verification_id,
                execution_id=execution_id,
                correlation_id=correlation_id,
                now=ts,
            )
        ]
        begun = await self._commit_with_audit(
            begin_audit,
            self._repo.begin_verification(
                remediation_id,
                verification_id=verification_id,
                pending=pending,
                audit_events=begin_audit,
            ),
        )

        outcome = await self._verifier.verify(
            target=begun.proposal.target, config=cfg, started_at=ts
        )
        final_status = (
            RemediationStatus.RECOVERED if outcome.recovered else RemediationStatus.RECOVERY_FAILED
        )
        checks_total = len(outcome.checks)
        result = VerificationResult(
            verification_id=verification_id,
            remediation_id=remediation_id,
            execution_id=execution_id,
            status=outcome.status,
            verifier_type=outcome.verifier_type,
            verifier_version=outcome.verifier_version,
            attempts=outcome.attempts,
            checks=outcome.checks,
            failure_reason=outcome.failure_reason,
            timeout_seconds=cfg.timeout_seconds,
            poll_interval_seconds=cfg.poll_interval_seconds,
            metadata={
                "probe_status": str(outcome.probe_status),
                "checks_passed": outcome.checks_passed,
                "checks_total": checks_total,
                "first_healthy_at": (
                    outcome.first_healthy_at.isoformat() if outcome.first_healthy_at else ""
                ),
            },
            started_at=ts,
            completed_at=outcome.completed_at,
        )
        finish_audit = [
            verification_finished_event(
                begun.proposal,
                verification_id=verification_id,
                execution_id=execution_id,
                final_state=final_status,
                attempts=outcome.attempts,
                checks_passed=outcome.checks_passed,
                checks_total=checks_total,
                failure_reason=outcome.failure_reason,
                verifier_type=outcome.verifier_type,
                correlation_id=correlation_id,
                now=ts,
            )
        ]
        completed = await self._commit_with_audit(
            finish_audit,
            self._repo.finish_verification(
                remediation_id,
                verification_id=verification_id,
                result=result,
                final_status=final_status,
                audit_events=finish_audit,
            ),
        )
        self._metrics.record_verification(
            final_status,
            duration_seconds=(outcome.completed_at - ts).total_seconds(),
        )
        logger.info(
            "recovery verification complete",
            extra={
                "remediation_id": remediation_id,
                "verification_id": verification_id,
                "new_status": str(final_status),
                "attempts": outcome.attempts,
                "checks_passed": outcome.checks_passed,
                "checks_total": checks_total,
            },
        )
        return VerificationOutcome(record=completed, result=result, replayed=False)

    @staticmethod
    def _check_verifiable(record: RemediationRecord) -> None:
        """Deterministic pre-lock guards. Re-checked atomically by
        ``repo.begin_verification`` (the ``EXECUTED -> VERIFYING`` claim)."""

        if record.status is not RemediationStatus.EXECUTED:
            raise InvalidRemediationStateError(
                f"remediation {record.remediation_id} is {record.status}, not EXECUTED"
            )
        if record.execution is None or record.execution.status is not ExecutionStatus.SUCCEEDED:
            raise InvalidRemediationStateError(
                f"remediation {record.remediation_id} has no successful execution to verify"
            )
