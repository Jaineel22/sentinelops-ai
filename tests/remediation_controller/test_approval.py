"""Approval model + the fail-closed execution guard (spec section 5)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from remediation_controller.domain import (
    ApprovalDecision,
    ApproverRole,
    RemediationApproval,
    RemediationProposal,
    RemediationStatus,
    authorize_execution,
    new_approval_id,
)
from remediation_controller.domain.errors import ApprovalError

_ApprovalFactory = Callable[..., RemediationApproval]
_ProposalFactory = Callable[..., RemediationProposal]
_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


def _approval_payload() -> dict[str, object]:
    return {
        "approval_id": "apr_00112233aabbccdd",
        "remediation_id": "rem_00112233aabbccdd",
        "decision": "APPROVE",
        "approver_identity": "alice@example.com",
        "approver_role": "INCIDENT_RESPONDER",
        "decided_at": "2026-09-02T12:00:00Z",
    }


def test_valid_approval_constructs() -> None:
    approval = RemediationApproval.model_validate(_approval_payload())
    assert approval.decision is ApprovalDecision.APPROVE
    assert approval.approver_role is ApproverRole.INCIDENT_RESPONDER


@pytest.mark.parametrize("identity", ["", "   ", "\t", "\n"])
def test_empty_or_blank_approver_identity_rejected(identity: str) -> None:
    with pytest.raises(ValidationError):
        RemediationApproval.model_validate({**_approval_payload(), "approver_identity": identity})


def test_approval_is_frozen_and_forbids_extra_fields() -> None:
    approval = RemediationApproval.model_validate(_approval_payload())
    with pytest.raises(ValidationError):
        approval.decision = ApprovalDecision.REJECT
    with pytest.raises(ValidationError):
        RemediationApproval.model_validate({**_approval_payload(), "override": True})


def test_authorize_execution_accepts_a_matching_approve(
    proposal_factory: _ProposalFactory, approval_factory: _ApprovalFactory
) -> None:
    proposal = proposal_factory().model_copy(update={"status": RemediationStatus.APPROVED})
    approval = approval_factory(remediation_id=proposal.remediation_id)
    authorize_execution(proposal, approval)  # does not raise


def test_authorize_execution_requires_approved_status(
    proposal_factory: _ProposalFactory, approval_factory: _ApprovalFactory
) -> None:
    proposal = proposal_factory()  # status PROPOSED
    approval = approval_factory(remediation_id=proposal.remediation_id)
    with pytest.raises(ApprovalError):
        authorize_execution(proposal, approval)


def test_authorize_execution_rejects_missing_approval(
    proposal_factory: _ProposalFactory,
) -> None:
    proposal = proposal_factory().model_copy(update={"status": RemediationStatus.APPROVED})
    with pytest.raises(ApprovalError):
        authorize_execution(proposal, None)


def test_authorize_execution_rejects_a_reject_decision(
    proposal_factory: _ProposalFactory, approval_factory: _ApprovalFactory
) -> None:
    proposal = proposal_factory().model_copy(update={"status": RemediationStatus.APPROVED})
    approval = approval_factory(
        remediation_id=proposal.remediation_id, decision=ApprovalDecision.REJECT
    )
    with pytest.raises(ApprovalError):
        authorize_execution(proposal, approval)


def test_authorize_execution_rejects_an_approval_for_another_remediation(
    proposal_factory: _ProposalFactory, approval_factory: _ApprovalFactory
) -> None:
    proposal = proposal_factory().model_copy(update={"status": RemediationStatus.APPROVED})
    other = RemediationApproval(
        approval_id=new_approval_id(),
        remediation_id="rem_ffeeddccbbaa9988",
        decision=ApprovalDecision.APPROVE,
        approver_identity="mallory@example.com",
        approver_role=ApproverRole.OPERATOR,
        decided_at=_NOW,
    )
    with pytest.raises(ApprovalError):
        authorize_execution(proposal, other)
