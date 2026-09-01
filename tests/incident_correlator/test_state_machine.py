"""Incident lifecycle state machine (ADR-017)."""

from __future__ import annotations

import pytest

from incident_correlator.domain import IncidentStatus
from incident_correlator.state_machine import (
    InvalidTransitionError,
    allowed_transitions,
    can_transition,
    validate_transition,
)

S = IncidentStatus


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.OPEN, S.ACKNOWLEDGED),
        (S.OPEN, S.INVESTIGATING),
        (S.OPEN, S.RESOLVED),
        (S.ACKNOWLEDGED, S.INVESTIGATING),
        (S.INVESTIGATING, S.MITIGATING),
        (S.MITIGATING, S.RESOLVED),
    ],
)
def test_valid_transitions(current: IncidentStatus, target: IncidentStatus) -> None:
    validate_transition(current, target, actor="api")


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.OPEN, S.MITIGATING),  # can't skip
        (S.ACKNOWLEDGED, S.OPEN),  # no going back
        (S.RESOLVED, S.OPEN),  # no reopening
        (S.RESOLVED, S.INVESTIGATING),
        (S.MITIGATING, S.INVESTIGATING),
    ],
)
def test_invalid_transitions_rejected(current: IncidentStatus, target: IncidentStatus) -> None:
    assert not can_transition(current, target, actor="api")
    with pytest.raises(InvalidTransitionError):
        validate_transition(current, target, actor="api")


def test_system_may_force_resolve_from_any_active_state() -> None:
    for s in (S.OPEN, S.ACKNOWLEDGED, S.INVESTIGATING, S.MITIGATING):
        assert can_transition(s, S.RESOLVED, actor="system")
    assert not can_transition(S.RESOLVED, S.RESOLVED, actor="system")


def test_resolved_is_terminal() -> None:
    assert allowed_transitions(S.RESOLVED) == frozenset()
