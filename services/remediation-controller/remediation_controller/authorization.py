"""Deterministic approval authorization (Phase 5C).

Which :class:`ApproverRole` may **approve** which remediation action. The check
is deterministic and derived from the **catalogue** risk level of the action —
never from the proposal's own declared risk field (which an upstream LLM mapping
set) and never from any free-text field. Same principle as the policy engine
(ADR-025).

This is a small demo authorization boundary, **not** an identity provider.
Approver identity is supplied by the API request and only structurally validated
(non-empty). Real authentication is a later concern; the interface is kept ready
for it.

Matrix (narrowest safe default — spec section "Authorization"):

    OPERATOR            -> may approve LOW risk actions
    INCIDENT_RESPONDER  -> may approve LOW + MEDIUM risk actions
    ADMINISTRATOR       -> may approve LOW + MEDIUM + HIGH risk actions

Any role may **reject** any remediation (rejecting is always safe).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from remediation_controller.domain.catalogue import require_action_definition
from remediation_controller.domain.enums import ApproverRole, RemediationActionType, RiskLevel

_MATRIX: dict[ApproverRole, frozenset[RiskLevel]] = {
    ApproverRole.OPERATOR: frozenset({RiskLevel.LOW}),
    ApproverRole.INCIDENT_RESPONDER: frozenset({RiskLevel.LOW, RiskLevel.MEDIUM}),
    ApproverRole.ADMINISTRATOR: frozenset({RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH}),
}

APPROVAL_MATRIX: Mapping[ApproverRole, frozenset[RiskLevel]] = MappingProxyType(_MATRIX)

# Every role is total over the enum (asserted so an incomplete edit fails fast).
assert set(APPROVAL_MATRIX) == set(ApproverRole)


def max_approvable_risk(role: ApproverRole) -> frozenset[RiskLevel]:
    return _MATRIX[role]


def can_approve(role: ApproverRole, action_type: RemediationActionType) -> bool:
    """Deterministic: is ``role`` permitted to approve ``action_type``?

    Uses the catalogue's risk classification, not the proposal's declared one.
    """

    definition = require_action_definition(action_type)
    return definition.risk_level in _MATRIX[role]
