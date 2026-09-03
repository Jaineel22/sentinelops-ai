"""Remediation lifecycle state machine (spec section 6). Fail closed."""

from __future__ import annotations

import pytest

from remediation_controller.domain import (
    TERMINAL_STATUSES,
    RemediationStatus,
    allowed_transitions,
    can_transition,
    validate_transition,
)
from remediation_controller.domain.errors import InvalidRemediationTransition

S = RemediationStatus


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.PROPOSED, S.POLICY_EVALUATION),
        (S.PROPOSED, S.BLOCKED),
        (S.POLICY_EVALUATION, S.PENDING_APPROVAL),
        (S.POLICY_EVALUATION, S.BLOCKED),
        (S.PENDING_APPROVAL, S.APPROVED),
        (S.PENDING_APPROVAL, S.REJECTED),
        (S.PENDING_APPROVAL, S.EXPIRED),
        (S.APPROVED, S.EXECUTING),
        (S.APPROVED, S.EXPIRED),
        (S.EXECUTING, S.EXECUTED),
        (S.EXECUTING, S.EXECUTION_FAILED),
        (S.EXECUTED, S.VERIFYING),
        (S.VERIFYING, S.RECOVERED),
        (S.VERIFYING, S.RECOVERY_FAILED),
    ],
)
def test_valid_transitions(current: RemediationStatus, target: RemediationStatus) -> None:
    validate_transition(current, target)
    assert can_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.PROPOSED, S.EXECUTING),  # must be approved first
        (S.PROPOSED, S.APPROVED),
        (S.PENDING_APPROVAL, S.EXECUTING),  # must be approved first
        (S.POLICY_EVALUATION, S.EXECUTING),
        (S.REJECTED, S.EXECUTING),  # terminal
        (S.EXECUTED, S.EXECUTING),  # no re-execution
        (S.RECOVERED, S.APPROVED),  # terminal
        (S.BLOCKED, S.PENDING_APPROVAL),  # terminal
        (S.EXPIRED, S.APPROVED),  # terminal
        (S.APPROVED, S.EXECUTED),  # cannot skip EXECUTING
        (S.EXECUTED, S.RECOVERED),  # cannot skip VERIFYING
        (S.VERIFYING, S.EXECUTING),
    ],
)
def test_invalid_transitions_rejected(
    current: RemediationStatus, target: RemediationStatus
) -> None:
    assert not can_transition(current, target)
    with pytest.raises(InvalidRemediationTransition):
        validate_transition(current, target)


def test_terminal_states_have_no_outgoing_transitions() -> None:
    for s in TERMINAL_STATUSES:
        assert allowed_transitions(s) == frozenset()


def test_executing_is_only_reachable_from_approved() -> None:
    predecessors = {s for s in RemediationStatus if can_transition(s, S.EXECUTING)}
    assert predecessors == {S.APPROVED}
    # ...and APPROVED is only reachable from PENDING_APPROVAL (i.e. after a human
    # decision has been recorded).
    approved_predecessors = {s for s in RemediationStatus if can_transition(s, S.APPROVED)}
    assert approved_predecessors == {S.PENDING_APPROVAL}
