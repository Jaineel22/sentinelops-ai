"""Investigation lifecycle state machine (ADR-019)."""

from __future__ import annotations

import pytest

from rca_agent.domain import ACTIVE_STATUSES, TERMINAL_STATUSES, InvestigationStatus
from rca_agent.state_machine import (
    InvalidInvestigationTransition,
    allowed_transitions,
    can_transition,
    validate_transition,
)

S = InvestigationStatus


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.PENDING, S.PLANNING),
        (S.PLANNING, S.COLLECTING_EVIDENCE),
        (S.COLLECTING_EVIDENCE, S.COLLECTING_EVIDENCE),  # bounded tool loop
        (S.COLLECTING_EVIDENCE, S.ANALYZING),
        (S.ANALYZING, S.ANALYZING),  # single re-analysis pass
        (S.ANALYZING, S.VERIFYING),
        (S.VERIFYING, S.ANALYZING),  # verification bounced it back
        (S.VERIFYING, S.COMPLETED),
        (S.PLANNING, S.INSUFFICIENT_EVIDENCE),
        (S.COLLECTING_EVIDENCE, S.TIMED_OUT),
        (S.ANALYZING, S.FAILED),
    ],
)
def test_valid_transitions(current: InvestigationStatus, target: InvestigationStatus) -> None:
    validate_transition(current, target)
    assert can_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.PENDING, S.COLLECTING_EVIDENCE),  # cannot skip planning
        (S.PENDING, S.COMPLETED),
        (S.PLANNING, S.COMPLETED),  # cannot complete without analysis
        (S.COLLECTING_EVIDENCE, S.COMPLETED),
        (S.COMPLETED, S.ANALYZING),  # terminal
        (S.FAILED, S.PENDING),
        (S.INSUFFICIENT_EVIDENCE, S.COLLECTING_EVIDENCE),
        (S.VERIFYING, S.COLLECTING_EVIDENCE),  # no going back to collection
    ],
)
def test_invalid_transitions_rejected(
    current: InvestigationStatus, target: InvestigationStatus
) -> None:
    assert not can_transition(current, target)
    with pytest.raises(InvalidInvestigationTransition):
        validate_transition(current, target)


def test_terminal_states_have_no_outgoing_transitions() -> None:
    for s in TERMINAL_STATUSES:
        assert allowed_transitions(s) == frozenset()
        assert s.is_terminal


def test_active_and_terminal_partition_the_enum() -> None:
    assert ACTIVE_STATUSES.isdisjoint(TERMINAL_STATUSES)
    assert set(InvestigationStatus) == ACTIVE_STATUSES | TERMINAL_STATUSES
    assert not any(s.is_terminal for s in ACTIVE_STATUSES)
