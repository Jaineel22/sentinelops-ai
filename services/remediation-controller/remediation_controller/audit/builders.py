"""Construct :class:`RemediationAuditEvent` values from workflow objects.

These are the **only** functions that mint audit events. Each takes already-
validated domain / policy / executor objects (never client input), routes every
free-text and structured value through
:mod:`remediation_controller.audit.redaction`, and returns an immutable event.

The service layer decides *which* events a lifecycle step produces and hands
them to the repository, which persists them **in the same transaction** as the
state change (see :meth:`RemediationRepository.create` etc.). This module has no
persistence and no I/O.
"""

from __future__ import annotations

from datetime import datetime

from remediation_controller.audit.model import (
    SYSTEM_ACTOR_ID,
    ActorType,
    AuditEventType,
    AuditMetadataValue,
    ExecutionMode,
    RemediationAuditEvent,
    new_audit_id,
)
from remediation_controller.audit.redaction import (
    redact_identity,
    redact_metadata,
    redact_text,
)
from remediation_controller.domain.enums import (
    ApproverRole,
    ExecutionStatus,
    RemediationStatus,
)
from remediation_controller.domain.models import RemediationApproval
from remediation_controller.domain.proposal import RemediationProposal
from remediation_controller.executor.base import ExecutionResult
from remediation_controller.policy.codes import PolicyOutcome, PolicyReasonCode
from remediation_controller.policy.decision import PolicyDecision


def _event(
    *,
    proposal: RemediationProposal,
    event_type: AuditEventType,
    actor_type: ActorType,
    now: datetime,
    correlation_id: str | None,
    actor_id: str = SYSTEM_ACTOR_ID,
    actor_role: ApproverRole | None = None,
    previous_state: RemediationStatus | None = None,
    new_state: RemediationStatus | None = None,
    policy_outcome: PolicyOutcome | None = None,
    policy_version: str | None = None,
    policy_reason_codes: tuple[PolicyReasonCode, ...] = (),
    execution_id: str | None = None,
    execution_mode: ExecutionMode | None = None,
    execution_result: ExecutionStatus | None = None,
    verification_id: str | None = None,
    reason: str = "",
    metadata: dict[str, AuditMetadataValue] | None = None,
) -> RemediationAuditEvent:
    return RemediationAuditEvent(
        audit_id=new_audit_id(),
        remediation_id=proposal.remediation_id,
        incident_id=proposal.incident_id,
        investigation_id=proposal.investigation_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_role=actor_role,
        previous_state=previous_state,
        new_state=new_state,
        action_type=proposal.action_type,
        target_service=proposal.target.service_name,
        target_environment=proposal.target.environment,
        policy_outcome=policy_outcome,
        policy_version=policy_version,
        policy_reason_codes=policy_reason_codes,
        execution_id=execution_id,
        execution_mode=execution_mode,
        execution_result=execution_result,
        verification_id=verification_id,
        reason=reason,
        correlation_id=correlation_id,
        metadata=metadata if metadata is not None else {},
        occurred_at=now,
    )


def proposal_created_event(
    proposal: RemediationProposal,
    *,
    correlation_id: str | None = None,
    now: datetime,
) -> RemediationAuditEvent:
    """``PROPOSED`` — the deterministic RCA→proposal mapping produced a proposal."""

    params: dict[str, AuditMetadataValue] = {
        "trigger": str(proposal.trigger),
        "proposed_by": proposal.proposed_by,
        "risk_level": str(proposal.risk_level),
        "source_recommendation": proposal.source_recommendation,
        "evidence_reference_count": len(proposal.evidence_references),
    }
    for key, value in proposal.parameters.items():
        params[f"param.{key}"] = value
    return _event(
        proposal=proposal,
        event_type=AuditEventType.PROPOSAL_CREATED,
        actor_type=ActorType.SYSTEM,
        now=now,
        correlation_id=correlation_id,
        previous_state=None,
        new_state=RemediationStatus.PROPOSED,
        reason=redact_text(proposal.reason),
        metadata=redact_metadata(params),
    )


def policy_evaluated_event(
    proposal: RemediationProposal,
    decision: PolicyDecision,
    *,
    new_state: RemediationStatus,
    correlation_id: str | None = None,
    now: datetime,
) -> RemediationAuditEvent:
    """``POLICY_EVALUATION`` → ``PENDING_APPROVAL`` | ``BLOCKED`` — the
    deterministic policy engine's decision, recorded verbatim (codes only)."""

    detail = "; ".join(v.detail for v in decision.violations)
    return _event(
        proposal=proposal,
        event_type=AuditEventType.POLICY_EVALUATED,
        actor_type=ActorType.SYSTEM,
        now=now,
        correlation_id=correlation_id,
        previous_state=RemediationStatus.POLICY_EVALUATION,
        new_state=new_state,
        policy_outcome=decision.outcome,
        policy_version=decision.policy_version,
        policy_reason_codes=decision.reason_codes,
        reason=redact_text(detail) if detail else f"policy {decision.outcome}",
        metadata=redact_metadata({"evaluated_rules": ",".join(decision.evaluated_rules)}),
    )


def remediation_blocked_event(
    proposal: RemediationProposal,
    decision: PolicyDecision,
    *,
    correlation_id: str | None = None,
    now: datetime,
) -> RemediationAuditEvent:
    """``BLOCKED`` — a first-class, queryable record that policy denied this
    remediation (it can never be approved or executed)."""

    detail = "; ".join(f"{v.code}:{v.detail}" for v in decision.violations)
    return _event(
        proposal=proposal,
        event_type=AuditEventType.REMEDIATION_BLOCKED,
        actor_type=ActorType.SYSTEM,
        now=now,
        correlation_id=correlation_id,
        previous_state=RemediationStatus.POLICY_EVALUATION,
        new_state=RemediationStatus.BLOCKED,
        policy_outcome=decision.outcome,
        policy_version=decision.policy_version,
        policy_reason_codes=decision.reason_codes,
        reason=redact_text(detail) or "policy denied the remediation",
    )


def decision_event(
    proposal: RemediationProposal,
    approval: RemediationApproval,
    *,
    previous_state: RemediationStatus = RemediationStatus.PENDING_APPROVAL,
    correlation_id: str | None = None,
    now: datetime,
) -> RemediationAuditEvent:
    """``APPROVED`` / ``REJECTED`` — an explicit, immutable human decision.

    ``actor_id`` is the value-redacted, capped approver identity; ``actor_role``
    is the role the deterministic authorization check was made against.
    """

    approved = approval.decision.value == "APPROVE"
    new_state = RemediationStatus.APPROVED if approved else RemediationStatus.REJECTED
    return _event(
        proposal=proposal,
        event_type=AuditEventType.APPROVED if approved else AuditEventType.REJECTED,
        actor_type=ActorType.HUMAN,
        now=now,
        correlation_id=correlation_id,
        actor_id=redact_identity(approval.approver_identity),
        actor_role=approval.approver_role,
        previous_state=previous_state,
        new_state=new_state,
        reason=redact_text(approval.reason),
        metadata=redact_metadata({"approval_id": approval.approval_id}),
    )


def execution_requested_event(
    proposal: RemediationProposal,
    *,
    execution_id: str,
    correlation_id: str | None = None,
    now: datetime,
) -> RemediationAuditEvent:
    """``EXECUTION_REQUESTED`` — a real execution of an ``APPROVED`` remediation
    was requested and passed the deterministic pre-execution guards."""

    return _event(
        proposal=proposal,
        event_type=AuditEventType.EXECUTION_REQUESTED,
        actor_type=ActorType.SYSTEM,
        now=now,
        correlation_id=correlation_id,
        previous_state=RemediationStatus.APPROVED,
        new_state=RemediationStatus.APPROVED,
        execution_id=execution_id,
        execution_mode=ExecutionMode.REAL,
        reason="execution requested for an approved remediation",
    )


def execution_started_event(
    proposal: RemediationProposal,
    *,
    execution_id: str,
    correlation_id: str | None = None,
    now: datetime,
) -> RemediationAuditEvent:
    """``APPROVED`` → ``EXECUTING`` — the single execution was claimed atomically
    and handed to the allow-listed executor."""

    return _event(
        proposal=proposal,
        event_type=AuditEventType.EXECUTION_STARTED,
        actor_type=ActorType.SYSTEM,
        now=now,
        correlation_id=correlation_id,
        previous_state=RemediationStatus.APPROVED,
        new_state=RemediationStatus.EXECUTING,
        execution_id=execution_id,
        execution_mode=ExecutionMode.REAL,
        reason="execution claimed; handed to LOCAL_SIMULATION executor",
    )


def execution_finished_event(
    proposal: RemediationProposal,
    result: ExecutionResult,
    *,
    final_state: RemediationStatus,
    correlation_id: str | None = None,
    now: datetime,
) -> RemediationAuditEvent:
    """``EXECUTING`` → ``EXECUTED`` (``EXECUTION_SUCCEEDED``) or
    ``EXECUTION_FAILED`` (``EXECUTION_FAILED``)."""

    succeeded = final_state is RemediationStatus.EXECUTED
    event_type = (
        AuditEventType.EXECUTION_SUCCEEDED if succeeded else AuditEventType.EXECUTION_FAILED
    )
    reason = (
        redact_text(result.simulated_effect)
        if succeeded
        else redact_text(result.error or "executor failed")
    )
    return _event(
        proposal=proposal,
        event_type=event_type,
        actor_type=ActorType.SYSTEM,
        now=now,
        correlation_id=correlation_id,
        previous_state=RemediationStatus.EXECUTING,
        new_state=final_state,
        execution_id=result.execution_id,
        execution_mode=ExecutionMode.REAL,
        execution_result=result.status,
        reason=reason,
        metadata=redact_metadata(
            {"executor_type": str(result.executor_type), "dry_run": result.dry_run}
        ),
    )


def verification_started_event(
    proposal: RemediationProposal,
    *,
    verification_id: str,
    execution_id: str,
    correlation_id: str | None = None,
    now: datetime,
) -> RemediationAuditEvent:
    """``EXECUTED`` → ``VERIFYING`` — recovery verification was requested and the
    single verification was claimed atomically."""

    return _event(
        proposal=proposal,
        event_type=AuditEventType.VERIFICATION_STARTED,
        actor_type=ActorType.SYSTEM,
        now=now,
        correlation_id=correlation_id,
        previous_state=RemediationStatus.EXECUTED,
        new_state=RemediationStatus.VERIFYING,
        execution_id=execution_id,
        verification_id=verification_id,
        reason="recovery verification started",
    )


def verification_finished_event(
    proposal: RemediationProposal,
    *,
    verification_id: str,
    execution_id: str,
    final_state: RemediationStatus,
    attempts: int,
    checks_passed: int,
    checks_total: int,
    failure_reason: str | None,
    verifier_type: str,
    correlation_id: str | None = None,
    now: datetime,
) -> RemediationAuditEvent:
    """``VERIFYING`` → ``RECOVERED`` (``VERIFICATION_SUCCEEDED``) or
    ``RECOVERY_FAILED`` (``VERIFICATION_FAILED``)."""

    recovered = final_state is RemediationStatus.RECOVERED
    event_type = (
        AuditEventType.VERIFICATION_SUCCEEDED if recovered else AuditEventType.VERIFICATION_FAILED
    )
    reason = (
        f"recovery verified: {checks_passed}/{checks_total} checks passed after {attempts} poll(s)"
        if recovered
        else redact_text(failure_reason or "recovery not verified within the window")
    )
    return _event(
        proposal=proposal,
        event_type=event_type,
        actor_type=ActorType.SYSTEM,
        now=now,
        correlation_id=correlation_id,
        previous_state=RemediationStatus.VERIFYING,
        new_state=final_state,
        execution_id=execution_id,
        verification_id=verification_id,
        reason=reason,
        metadata=redact_metadata(
            {
                "attempts": attempts,
                "checks_passed": checks_passed,
                "checks_total": checks_total,
                "verifier_type": verifier_type,
            }
        ),
    )


__all__ = [
    "decision_event",
    "execution_finished_event",
    "execution_requested_event",
    "execution_started_event",
    "policy_evaluated_event",
    "proposal_created_event",
    "remediation_blocked_event",
    "verification_finished_event",
    "verification_started_event",
]
