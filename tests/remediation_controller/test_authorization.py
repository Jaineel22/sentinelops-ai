"""Deterministic approval authorization matrix (Phase 5C)."""

from __future__ import annotations

import pytest

from remediation_controller.authorization import APPROVAL_MATRIX, can_approve, max_approvable_risk
from remediation_controller.domain import ApproverRole, RemediationActionType, RiskLevel


def test_matrix_is_total_and_code_defined() -> None:
    assert set(APPROVAL_MATRIX) == set(ApproverRole)


def test_matrix_is_read_only() -> None:
    with pytest.raises(TypeError):
        APPROVAL_MATRIX[ApproverRole.OPERATOR] = frozenset()  # type: ignore[index]


@pytest.mark.parametrize(
    ("role", "risks"),
    [
        (ApproverRole.OPERATOR, {RiskLevel.LOW}),
        (ApproverRole.INCIDENT_RESPONDER, {RiskLevel.LOW, RiskLevel.MEDIUM}),
        (ApproverRole.ADMINISTRATOR, {RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH}),
    ],
)
def test_max_approvable_risk(role: ApproverRole, risks: set[RiskLevel]) -> None:
    assert max_approvable_risk(role) == risks


@pytest.mark.parametrize(
    ("role", "action", "allowed"),
    [
        # DISABLE_FEATURE_FLAG is LOW risk
        (ApproverRole.OPERATOR, RemediationActionType.DISABLE_FEATURE_FLAG, True),
        # RESTART_SERVICE / SCALE_SERVICE are MEDIUM
        (ApproverRole.OPERATOR, RemediationActionType.RESTART_SERVICE, False),
        (ApproverRole.INCIDENT_RESPONDER, RemediationActionType.RESTART_SERVICE, True),
        (ApproverRole.INCIDENT_RESPONDER, RemediationActionType.SCALE_SERVICE, True),
        # ROLL_BACK_DEPLOYMENT is HIGH
        (ApproverRole.INCIDENT_RESPONDER, RemediationActionType.ROLL_BACK_DEPLOYMENT, False),
        (ApproverRole.ADMINISTRATOR, RemediationActionType.ROLL_BACK_DEPLOYMENT, True),
    ],
)
def test_can_approve(role: ApproverRole, action: RemediationActionType, allowed: bool) -> None:
    assert can_approve(role, action) is allowed


def test_can_approve_uses_catalogue_risk() -> None:
    # sanity: the source references the catalogue definition, not proposal.risk_level
    from pathlib import Path

    from remediation_controller import authorization

    src = Path(authorization.__file__).read_text(encoding="utf-8")
    assert "require_action_definition" in src
    assert "proposal.risk_level" not in src
