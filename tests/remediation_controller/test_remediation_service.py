"""RemediationService — propose + decide orchestration (Phase 5C)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from remediation_controller.domain import ApprovalDecision, ApproverRole, RemediationStatus
from remediation_controller.domain.proposal import RcaRecommendedActionInput
from remediation_controller.repository import (
    InMemoryRemediationRepository,
    InvalidRemediationStateError,
    ProposalNotMappableError,
    RemediationExpiredError,
    RemediationFilter,
    RemediationNotFoundError,
    UnauthorizedApproverError,
)
from remediation_controller.service import RemediationService
from tests.remediation_controller.policy_fakes import BASE_TIME

_INCIDENT = "inc_00112233aabbccdd"


def _svc() -> tuple[RemediationService, InMemoryRemediationRepository]:
    repo = InMemoryRemediationRepository()
    return RemediationService(repository=repo), repo


def _rec(
    action: str = "RESTART_SERVICE", target: str | None = "orders-service"
) -> RcaRecommendedActionInput:
    return RcaRecommendedActionInput(
        action_type=action, target_service=target, rationale="pool saturation"
    )


async def test_propose_policy_allows_pends_for_approval() -> None:
    svc, _ = _svc()
    record = await svc.propose(
        incident_id=_INCIDENT, recommendation=_rec(), incident_severity="HIGH", now=BASE_TIME
    )
    assert record.status is RemediationStatus.PENDING_APPROVAL
    assert record.policy_decision.outcome.value == "ALLOW"
    assert record.proposal.requires_approval is True


async def test_propose_policy_denies_blocks() -> None:
    svc, _ = _svc()
    # environment is a pure policy concern (5B), not checked by the 5A mapping —
    # so a staging target produces a *persisted* BLOCKED remediation, not a 422.
    record = await svc.propose(
        incident_id=_INCIDENT,
        recommendation=_rec(),
        incident_severity="HIGH",
        target_environment="staging",
        now=BASE_TIME,
    )
    assert record.status is RemediationStatus.BLOCKED
    assert record.policy_decision.outcome.value == "DENY"
    assert "ENVIRONMENT_NOT_ALLOWED" in [str(c) for c in record.policy_decision.reason_codes]


async def test_propose_missing_severity_is_persisted_blocked() -> None:
    svc, _ = _svc()
    record = await svc.propose(
        incident_id=_INCIDENT, recommendation=_rec(), incident_severity=None, now=BASE_TIME
    )
    assert record.status is RemediationStatus.BLOCKED
    assert "SEVERITY_NOT_ALLOWED" in [str(c) for c in record.policy_decision.reason_codes]


async def test_propose_ineligible_severity_is_unmappable_422() -> None:
    svc, repo = _svc()
    with pytest.raises(ProposalNotMappableError):
        await svc.propose(
            incident_id=_INCIDENT, recommendation=_rec(), incident_severity="LOW", now=BASE_TIME
        )
    assert await repo.list(RemediationFilter()) == []


async def test_propose_unmappable_recommendation_raises_and_persists_nothing() -> None:
    svc, repo = _svc()
    with pytest.raises(ProposalNotMappableError):
        await svc.propose(
            incident_id=_INCIDENT,
            recommendation=_rec(action="INVESTIGATE_FURTHER"),
            incident_severity="HIGH",
        )
    assert await repo.list(RemediationFilter()) == []


async def test_propose_adversarial_label_is_unmappable() -> None:
    svc, _ = _svc()
    with pytest.raises(ProposalNotMappableError):
        await svc.propose(
            incident_id=_INCIDENT,
            recommendation=_rec(action="kubectl delete deployment orders-service"),
            incident_severity="HIGH",
        )


async def test_decide_approve_transitions_to_approved_only() -> None:
    svc, _ = _svc()
    record = await svc.propose(
        incident_id=_INCIDENT, recommendation=_rec(), incident_severity="HIGH", now=BASE_TIME
    )
    decided = await svc.decide(
        record.remediation_id,
        decision=ApprovalDecision.APPROVE,
        approver_identity="alice@example.com",
        approver_role=ApproverRole.INCIDENT_RESPONDER,
        now=BASE_TIME + timedelta(minutes=1),
    )
    assert decided.status is RemediationStatus.APPROVED  # never EXECUTING/EXECUTED
    assert decided.approval is not None
    assert decided.approval.approver_identity == "alice@example.com"


async def test_decide_reject_transitions_to_rejected() -> None:
    svc, _ = _svc()
    record = await svc.propose(
        incident_id=_INCIDENT, recommendation=_rec(), incident_severity="HIGH", now=BASE_TIME
    )
    decided = await svc.decide(
        record.remediation_id,
        decision=ApprovalDecision.REJECT,
        approver_identity="carol",
        approver_role=ApproverRole.OPERATOR,  # any role may reject
        now=BASE_TIME + timedelta(minutes=1),
    )
    assert decided.status is RemediationStatus.REJECTED


async def test_decide_twice_is_rejected() -> None:
    svc, _ = _svc()
    record = await svc.propose(
        incident_id=_INCIDENT, recommendation=_rec(), incident_severity="HIGH", now=BASE_TIME
    )
    await svc.decide(
        record.remediation_id,
        decision=ApprovalDecision.APPROVE,
        approver_identity="alice",
        approver_role=ApproverRole.INCIDENT_RESPONDER,
        now=BASE_TIME + timedelta(minutes=1),
    )
    with pytest.raises(InvalidRemediationStateError):
        await svc.decide(
            record.remediation_id,
            decision=ApprovalDecision.APPROVE,
            approver_identity="bob",
            approver_role=ApproverRole.ADMINISTRATOR,
            now=BASE_TIME + timedelta(minutes=2),
        )


async def test_decide_after_expiry_is_rejected() -> None:
    svc, _ = _svc()
    record = await svc.propose(
        incident_id=_INCIDENT, recommendation=_rec(), incident_severity="HIGH", now=BASE_TIME
    )
    with pytest.raises(RemediationExpiredError):
        await svc.decide(
            record.remediation_id,
            decision=ApprovalDecision.APPROVE,
            approver_identity="alice",
            approver_role=ApproverRole.INCIDENT_RESPONDER,
            now=BASE_TIME + timedelta(hours=3),
        )


async def test_decide_blocked_proposal_cannot_be_approved() -> None:
    svc, _ = _svc()
    record = await svc.propose(
        incident_id=_INCIDENT,
        recommendation=_rec(),
        incident_severity="HIGH",
        target_environment="staging",
        now=BASE_TIME,
    )
    assert record.status is RemediationStatus.BLOCKED
    with pytest.raises(InvalidRemediationStateError):
        await svc.decide(
            record.remediation_id,
            decision=ApprovalDecision.APPROVE,
            approver_identity="alice",
            approver_role=ApproverRole.ADMINISTRATOR,
            now=BASE_TIME + timedelta(minutes=1),
        )


async def test_decide_unauthorized_role_is_rejected() -> None:
    svc, _ = _svc()
    # ROLL_BACK_DEPLOYMENT is HIGH risk; OPERATOR may not approve it.
    record = await svc.propose(
        incident_id=_INCIDENT,
        recommendation=_rec(action="ROLL_BACK_DEPLOYMENT"),
        incident_severity="HIGH",
        now=BASE_TIME,
    )
    assert record.status is RemediationStatus.PENDING_APPROVAL
    with pytest.raises(UnauthorizedApproverError):
        await svc.decide(
            record.remediation_id,
            decision=ApprovalDecision.APPROVE,
            approver_identity="olly",
            approver_role=ApproverRole.OPERATOR,
            now=BASE_TIME + timedelta(minutes=1),
        )
    # but that same operator MAY reject it
    rejected = await svc.decide(
        record.remediation_id,
        decision=ApprovalDecision.REJECT,
        approver_identity="olly",
        approver_role=ApproverRole.OPERATOR,
        now=BASE_TIME + timedelta(minutes=2),
    )
    assert rejected.status is RemediationStatus.REJECTED


async def test_decide_unknown_remediation() -> None:
    svc, _ = _svc()
    with pytest.raises(RemediationNotFoundError):
        await svc.decide(
            "rem_ffffffffffffffff",
            decision=ApprovalDecision.APPROVE,
            approver_identity="x",
            approver_role=ApproverRole.ADMINISTRATOR,
        )
