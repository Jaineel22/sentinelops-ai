"""Persistence boundary for remediations (Phase 5C).

The domain (5A) and policy (5B) layers never touch SQLAlchemy. Everything goes
through :class:`RemediationRepository`. Two implementations, proven equivalent by
a test:

* :class:`InMemoryRemediationRepository` — unit tests.
* ``remediation_controller.db.SqlRemediationRepository`` — PostgreSQL (SQLite in
  fast repo tests).

A persisted remediation is a :class:`RemediationRecord`: the 5A
:class:`RemediationProposal` (the pure intent object, unchanged) + its
deterministic :class:`PolicyDecision` + — once a human has acted — the immutable
:class:`RemediationApproval`.

The repository also satisfies the Phase 5B
:class:`~remediation_controller.policy.RemediationHistoryPort` so the cooldown /
duplicate rules can read prior-remediation state. Because that port is
synchronous (ADR-025) and the SQL repo is async, callers pre-load a
:class:`RemediationHistorySnapshot` for one ``(incident, action, target)`` and
hand *that* to the policy engine.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from remediation_controller.audit.model import RemediationAuditEvent
from remediation_controller.domain.enums import RemediationActionType, RemediationStatus
from remediation_controller.domain.errors import RemediationDomainError
from remediation_controller.domain.models import RemediationApproval, ServiceTarget
from remediation_controller.domain.proposal import RemediationProposal
from remediation_controller.domain.state_machine import validate_transition
from remediation_controller.executor.base import ExecutionResult
from remediation_controller.policy.decision import PolicyDecision
from remediation_controller.recovery.model import VerificationResult

# A remediation for a given (incident, action, target) that has not reached a
# terminal state — used for duplicate protection (spec section 10).
_ACTIVE_PRE_EXECUTION = frozenset(
    {
        RemediationStatus.PROPOSED,
        RemediationStatus.POLICY_EVALUATION,
        RemediationStatus.PENDING_APPROVAL,
        RemediationStatus.APPROVED,
        RemediationStatus.EXECUTING,
        RemediationStatus.EXECUTED,
        RemediationStatus.VERIFYING,
    }
)
# Statuses that count as "a remediation ran" for the cooldown window. 5C had only
# APPROVED (a human authorised it); 5D adds the post-execution states.
_COOLDOWN_ANCHOR_STATUSES = frozenset(
    {
        RemediationStatus.APPROVED,
        RemediationStatus.EXECUTING,
        RemediationStatus.EXECUTED,
        RemediationStatus.EXECUTION_FAILED,
    }
)


# --- errors ----------------------------------------------------------
class RemediationNotFoundError(RemediationDomainError):
    """No remediation with that id."""


class RemediationAlreadyDecidedError(RemediationDomainError):
    """A human decision already exists for this remediation — it is immutable."""


class InvalidRemediationStateError(RemediationDomainError):
    """The remediation is not in a state from which this operation is allowed."""


class RemediationExpiredError(RemediationDomainError):
    """The approval window (``expires_at``) has elapsed."""


class RemediationPolicyBlockedError(RemediationDomainError):
    """Policy denied this remediation; it can never be approved."""


class UnauthorizedApproverError(RemediationDomainError):
    """The approver's role may not approve an action of this risk level."""


class ProposalNotMappableError(RemediationDomainError):
    """The RCA recommendation could not be mapped to a catalogue action/target.

    Carries the deterministic block reason from
    :func:`~remediation_controller.domain.proposal.proposal_from_rca`.
    """


class RemediationExecutionConflictError(RemediationDomainError):
    """Another execution for this remediation has already started or finished —
    the remediation is no longer ``APPROVED`` (Phase 5D idempotency guard)."""


class RecoveryVerificationConflictError(RemediationDomainError):
    """A recovery verification for this remediation is already in progress or
    complete — the remediation is no longer ``EXECUTED`` (Phase 5F idempotency
    guard). A repeated verification of an already-verified remediation is
    replayed by the service, not raised."""


# --- persisted record ----------------------------------------------
class RemediationRecord(BaseModel):
    """The repository / API view of one remediation."""

    model_config = ConfigDict(frozen=True)

    proposal: RemediationProposal
    policy_decision: PolicyDecision
    approval: RemediationApproval | None = None
    execution: ExecutionResult | None = None  # the (single) real execution, once started
    verification: VerificationResult | None = None  # the (single) recovery verification — 5F

    @property
    def remediation_id(self) -> str:
        return self.proposal.remediation_id

    @property
    def incident_id(self) -> str:
        return self.proposal.incident_id

    @property
    def status(self) -> RemediationStatus:
        return self.proposal.status

    def with_proposal(self, proposal: RemediationProposal) -> RemediationRecord:
        return self.model_copy(update={"proposal": proposal})

    def with_approval(self, approval: RemediationApproval) -> RemediationRecord:
        return self.model_copy(update={"approval": approval})

    def with_status(self, status: RemediationStatus) -> RemediationRecord:
        return self.with_proposal(self.proposal.model_copy(update={"status": status}))

    def with_execution(self, execution: ExecutionResult) -> RemediationRecord:
        return self.model_copy(update={"execution": execution})

    def with_verification(self, verification: VerificationResult) -> RemediationRecord:
        return self.model_copy(update={"verification": verification})


@dataclass(frozen=True)
class RemediationFilter:
    incident_id: str | None = None
    status: RemediationStatus | None = None
    action_type: RemediationActionType | None = None
    since: datetime | None = None
    limit: int = 50
    offset: int = 0


# --- synchronous history snapshot (bridges async repo -> sync policy) ---
@dataclass(frozen=True)
class RemediationHistorySnapshot:
    """A point-in-time answer to the ``RemediationHistoryPort`` queries for ONE
    ``(incident, action, target)``. Built by the async repository and handed to
    the synchronous :class:`~remediation_controller.policy.PolicyEngine`."""

    active_exists: bool = False
    last_completed: datetime | None = None

    def active_remediation_exists(
        self,
        *,
        incident_id: str,
        action_type: RemediationActionType,
        target: ServiceTarget,
    ) -> bool:
        return self.active_exists

    def last_completed_at(
        self,
        *,
        incident_id: str,
        action_type: RemediationActionType,
        target: ServiceTarget,
    ) -> datetime | None:
        return self.last_completed


# --- repository protocol ------------------------------------------
class RemediationRepository(Protocol):
    async def create(
        self,
        record: RemediationRecord,
        *,
        audit_events: Sequence[RemediationAuditEvent] = (),
    ) -> RemediationRecord:
        """Persist the new remediation and append ``audit_events`` **in the same
        transaction** (Phase 5E) — a committed proposal can never be missing its
        audit records."""

    async def get(self, remediation_id: str) -> RemediationRecord | None: ...

    async def list(self, flt: RemediationFilter) -> list[RemediationRecord]: ...

    async def record_decision(
        self,
        remediation_id: str,
        *,
        new_proposal: RemediationProposal,
        approval: RemediationApproval,
        audit_events: Sequence[RemediationAuditEvent] = (),
    ) -> RemediationRecord:
        """Atomically attach the (immutable) approval, persist the transitioned
        proposal, and append ``audit_events``. Must hold a row lock so two
        concurrent decisions cannot both win. Raises
        :class:`RemediationAlreadyDecidedError` for the loser (whose audit
        events roll back with the rest of its transaction)."""

    async def begin_execution(
        self,
        remediation_id: str,
        *,
        execution_id: str,
        pending: ExecutionResult,
        audit_events: Sequence[RemediationAuditEvent] = (),
    ) -> RemediationRecord:
        """Atomically claim the single execution: transition ``APPROVED ->
        EXECUTING``, insert the ``STARTED`` execution row, and append
        ``audit_events``, under a row lock.

        Raises :class:`RemediationNotFoundError`, or
        :class:`RemediationExecutionConflictError` if the remediation is no
        longer ``APPROVED`` (another execution already started/finished). The
        caller has already run the domain guards (``authorize_execution``,
        catalogue, target, params, expiry) on a pre-lock read."""

    async def finish_execution(
        self,
        remediation_id: str,
        *,
        execution_id: str,
        result: ExecutionResult,
        final_status: RemediationStatus,
        audit_events: Sequence[RemediationAuditEvent] = (),
    ) -> RemediationRecord:
        """Atomically transition ``EXECUTING -> EXECUTED | EXECUTION_FAILED``,
        write the terminal execution result, and append ``audit_events``, under
        a row lock."""

    async def begin_verification(
        self,
        remediation_id: str,
        *,
        verification_id: str,
        pending: VerificationResult,
        audit_events: Sequence[RemediationAuditEvent] = (),
    ) -> RemediationRecord:
        """Atomically claim the single recovery verification: transition
        ``EXECUTED -> VERIFYING`` and insert the ``STARTED``
        ``remediation_verifications`` row, and append ``audit_events``, under a
        row lock.

        Raises :class:`RemediationNotFoundError`, or
        :class:`RecoveryVerificationConflictError` if the remediation is no
        longer ``EXECUTED`` (a verification already started/finished)."""

    async def finish_verification(
        self,
        remediation_id: str,
        *,
        verification_id: str,
        result: VerificationResult,
        final_status: RemediationStatus,
        audit_events: Sequence[RemediationAuditEvent] = (),
    ) -> RemediationRecord:
        """Atomically transition ``VERIFYING -> RECOVERED | RECOVERY_FAILED``,
        write the terminal verification result, and append ``audit_events``,
        under a row lock."""

    async def list_audit_events(
        self,
        remediation_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[RemediationAuditEvent]:
        """Return this remediation's audit trail in chronological order (oldest
        first). Read-only — there is no update / delete / append-by-id method."""

    async def history_snapshot(
        self,
        *,
        incident_id: str,
        action_type: RemediationActionType,
        target: ServiceTarget,
    ) -> RemediationHistorySnapshot: ...

    async def health_check(self) -> bool: ...


# --- in-memory implementation ------------------------------------
@dataclass
class _Store:
    records: dict[str, RemediationRecord] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    executions: dict[str, ExecutionResult] = field(default_factory=dict)  # by remediation_id
    verifications: dict[str, VerificationResult] = field(default_factory=dict)  # by remediation_id
    audit: list[RemediationAuditEvent] = field(default_factory=list)  # append-only, global order


class InMemoryRemediationRepository:
    def __init__(self) -> None:
        self._store = _Store()

    def _append_audit(self, events: Sequence[RemediationAuditEvent]) -> None:
        # Mirrors the SQL repo: audit rows are appended within the same logical
        # operation as the state change. Only ever appended, never mutated.
        self._store.audit.extend(events)

    async def create(
        self,
        record: RemediationRecord,
        *,
        audit_events: Sequence[RemediationAuditEvent] = (),
    ) -> RemediationRecord:
        rid = record.remediation_id
        if rid in self._store.records:  # pragma: no cover - ids are random
            raise RemediationDomainError(f"remediation {rid} already exists")
        self._store.records[rid] = record
        self._store.order.append(rid)
        self._append_audit(audit_events)
        return copy.deepcopy(record)

    async def get(self, remediation_id: str) -> RemediationRecord | None:
        rec = self._store.records.get(remediation_id)
        return copy.deepcopy(rec) if rec is not None else None

    async def list(self, flt: RemediationFilter) -> list[RemediationRecord]:
        rows = [self._store.records[i] for i in self._store.order]
        if flt.incident_id is not None:
            rows = [r for r in rows if r.incident_id == flt.incident_id]
        if flt.status is not None:
            rows = [r for r in rows if r.status == flt.status]
        if flt.action_type is not None:
            rows = [r for r in rows if r.proposal.action_type == flt.action_type]
        if flt.since is not None:
            rows = [r for r in rows if r.proposal.created_at >= flt.since]
        rows.sort(key=lambda r: r.proposal.created_at, reverse=True)
        return [copy.deepcopy(r) for r in rows[flt.offset : flt.offset + flt.limit]]

    async def record_decision(
        self,
        remediation_id: str,
        *,
        new_proposal: RemediationProposal,
        approval: RemediationApproval,
        audit_events: Sequence[RemediationAuditEvent] = (),
    ) -> RemediationRecord:
        rec = self._store.records.get(remediation_id)
        if rec is None:
            raise RemediationNotFoundError(remediation_id)
        if rec.approval is not None:
            raise RemediationAlreadyDecidedError(remediation_id)
        updated = rec.with_proposal(new_proposal).with_approval(approval)
        self._store.records[remediation_id] = updated
        self._append_audit(audit_events)
        return copy.deepcopy(updated)

    async def begin_execution(
        self,
        remediation_id: str,
        *,
        execution_id: str,
        pending: ExecutionResult,
        audit_events: Sequence[RemediationAuditEvent] = (),
    ) -> RemediationRecord:
        rec = self._store.records.get(remediation_id)
        if rec is None:
            raise RemediationNotFoundError(remediation_id)
        if rec.status is not RemediationStatus.APPROVED or remediation_id in self._store.executions:
            raise RemediationExecutionConflictError(remediation_id)
        validate_transition(RemediationStatus.APPROVED, RemediationStatus.EXECUTING)
        updated = rec.with_status(RemediationStatus.EXECUTING).with_execution(pending)
        self._store.records[remediation_id] = updated
        self._store.executions[remediation_id] = pending
        self._append_audit(audit_events)
        return copy.deepcopy(updated)

    async def finish_execution(
        self,
        remediation_id: str,
        *,
        execution_id: str,
        result: ExecutionResult,
        final_status: RemediationStatus,
        audit_events: Sequence[RemediationAuditEvent] = (),
    ) -> RemediationRecord:
        rec = self._store.records.get(remediation_id)
        if rec is None:  # pragma: no cover - caller just began it
            raise RemediationNotFoundError(remediation_id)
        if rec.status is not RemediationStatus.EXECUTING:  # pragma: no cover - defensive
            raise RemediationExecutionConflictError(remediation_id)
        validate_transition(RemediationStatus.EXECUTING, final_status)
        updated = rec.with_status(final_status).with_execution(result)
        self._store.records[remediation_id] = updated
        self._store.executions[remediation_id] = result
        self._append_audit(audit_events)
        return copy.deepcopy(updated)

    async def begin_verification(
        self,
        remediation_id: str,
        *,
        verification_id: str,
        pending: VerificationResult,
        audit_events: Sequence[RemediationAuditEvent] = (),
    ) -> RemediationRecord:
        rec = self._store.records.get(remediation_id)
        if rec is None:
            raise RemediationNotFoundError(remediation_id)
        if (
            rec.status is not RemediationStatus.EXECUTED
            or remediation_id in self._store.verifications
        ):
            raise RecoveryVerificationConflictError(remediation_id)
        validate_transition(RemediationStatus.EXECUTED, RemediationStatus.VERIFYING)
        updated = rec.with_status(RemediationStatus.VERIFYING).with_verification(pending)
        self._store.records[remediation_id] = updated
        self._store.verifications[remediation_id] = pending
        self._append_audit(audit_events)
        return copy.deepcopy(updated)

    async def finish_verification(
        self,
        remediation_id: str,
        *,
        verification_id: str,
        result: VerificationResult,
        final_status: RemediationStatus,
        audit_events: Sequence[RemediationAuditEvent] = (),
    ) -> RemediationRecord:
        rec = self._store.records.get(remediation_id)
        if rec is None:  # pragma: no cover - caller just began it
            raise RemediationNotFoundError(remediation_id)
        if rec.status is not RemediationStatus.VERIFYING:  # pragma: no cover - defensive
            raise RecoveryVerificationConflictError(remediation_id)
        validate_transition(RemediationStatus.VERIFYING, final_status)
        updated = rec.with_status(final_status).with_verification(result)
        self._store.records[remediation_id] = updated
        self._store.verifications[remediation_id] = result
        self._append_audit(audit_events)
        return copy.deepcopy(updated)

    async def list_audit_events(
        self,
        remediation_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[RemediationAuditEvent]:
        events = [e for e in self._store.audit if e.remediation_id == remediation_id]
        return copy.deepcopy(events[offset : offset + limit])

    async def history_snapshot(
        self,
        *,
        incident_id: str,
        action_type: RemediationActionType,
        target: ServiceTarget,
    ) -> RemediationHistorySnapshot:
        active = False
        last_completed: datetime | None = None
        for rec in self._store.records.values():
            p = rec.proposal
            if (
                p.incident_id != incident_id
                or p.action_type != action_type
                or p.target.service_name != target.service_name
                or p.target.environment != target.environment
            ):
                continue
            if p.status in _ACTIVE_PRE_EXECUTION:
                active = True
            if p.status in _COOLDOWN_ANCHOR_STATUSES and rec.approval is not None:
                ts = rec.approval.decided_at
                if last_completed is None or ts > last_completed:
                    last_completed = ts
        return RemediationHistorySnapshot(active_exists=active, last_completed=last_completed)

    async def health_check(self) -> bool:
        return True


def utcnow() -> datetime:
    return datetime.now(tz=UTC)
