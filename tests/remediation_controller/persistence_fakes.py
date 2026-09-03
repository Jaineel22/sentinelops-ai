"""Helpers for Phase 5C / 5D persistence / service / API tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from remediation_controller.domain import (
    ApprovalDecision,
    ApproverRole,
    RemediationApproval,
    RemediationProposal,
    RemediationStatus,
    new_approval_id,
)
from remediation_controller.policy import PolicyEngine
from remediation_controller.policy.codes import PolicyOutcome, PolicyReasonCode
from remediation_controller.policy.decision import PolicyDecision
from remediation_controller.repository import RemediationRecord
from tests.remediation_controller.conftest import make_proposal

BASE_TIME = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


def allow_decision(*, at: datetime = BASE_TIME) -> PolicyDecision:
    return PolicyDecision(
        outcome=PolicyOutcome.ALLOW,
        reason_codes=(PolicyReasonCode.POLICY_OK, PolicyReasonCode.APPROVAL_REQUIRED),
        violations=(),
        policy_version="1",
        evaluated_rules=("state_rule", "action_rule"),
        evaluated_at=at,
    )


def deny_decision(*, at: datetime = BASE_TIME) -> PolicyDecision:
    return PolicyDecision(
        outcome=PolicyOutcome.DENY,
        reason_codes=(PolicyReasonCode.SEVERITY_NOT_ALLOWED,),
        violations=(),
        policy_version="1",
        evaluated_rules=("state_rule", "severity_rule"),
        evaluated_at=at,
    )


def make_record(
    *,
    status: RemediationStatus = RemediationStatus.PENDING_APPROVAL,
    decision: PolicyDecision | None = None,
    proposal: RemediationProposal | None = None,
    expires_in: timedelta = timedelta(hours=1),
    **proposal_kw: object,
) -> RemediationRecord:
    p = proposal or make_proposal(**proposal_kw)  # type: ignore[arg-type]
    p = p.model_copy(update={"status": status, "expires_at": p.created_at + expires_in})
    return RemediationRecord(
        proposal=p,
        policy_decision=decision or allow_decision(at=p.created_at),
    )


def make_approved_record(
    *,
    status: RemediationStatus = RemediationStatus.APPROVED,
    approver_role: ApproverRole = ApproverRole.ADMINISTRATOR,
    decided_at: datetime = BASE_TIME,
    expires_in: timedelta = timedelta(hours=1),
    **proposal_kw: object,
) -> RemediationRecord:
    """A record carrying a matching immutable APPROVE approval — the starting
    point for execution tests (default status APPROVED)."""

    rec = make_record(status=status, expires_in=expires_in, **proposal_kw)  # type: ignore[arg-type]
    approval = RemediationApproval(
        approval_id=new_approval_id(),
        remediation_id=rec.remediation_id,
        decision=ApprovalDecision.APPROVE,
        approver_identity="alice@example.com",
        approver_role=approver_role,
        reason="reviewed",
        decided_at=decided_at,
    )
    return rec.with_approval(approval)


def real_allow_for(proposal: RemediationProposal, *, severity: str = "HIGH") -> PolicyDecision:
    """A genuine PolicyEngine decision for a proposal (for end-to-end style checks)."""

    from remediation_controller.policy import PolicyContext
    from remediation_controller.repository import RemediationHistorySnapshot

    ctx = PolicyContext(
        now=proposal.created_at,
        incident_severity=severity,
        history=RemediationHistorySnapshot(),
    )
    return PolicyEngine().evaluate(proposal, ctx)
