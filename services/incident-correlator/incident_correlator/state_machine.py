"""Incident lifecycle state machine (ADR-017).

    OPEN -> ACKNOWLEDGED | INVESTIGATING | RESOLVED
    ACKNOWLEDGED -> INVESTIGATING | RESOLVED
    INVESTIGATING -> MITIGATING | RESOLVED
    MITIGATING -> RESOLVED
    RESOLVED -> (terminal)

The ``system`` actor may additionally force any active status -> RESOLVED
(auto-resolve when an incident goes stale). There is **no reopening**: a new
correlated anomaly after resolution creates a new incident.
"""

from __future__ import annotations

from incident_correlator.domain import IncidentStatus

_ALLOWED: dict[IncidentStatus, frozenset[IncidentStatus]] = {
    IncidentStatus.OPEN: frozenset(
        {IncidentStatus.ACKNOWLEDGED, IncidentStatus.INVESTIGATING, IncidentStatus.RESOLVED}
    ),
    IncidentStatus.ACKNOWLEDGED: frozenset({IncidentStatus.INVESTIGATING, IncidentStatus.RESOLVED}),
    IncidentStatus.INVESTIGATING: frozenset({IncidentStatus.MITIGATING, IncidentStatus.RESOLVED}),
    IncidentStatus.MITIGATING: frozenset({IncidentStatus.RESOLVED}),
    IncidentStatus.RESOLVED: frozenset(),
}


class InvalidTransitionError(ValueError):
    """The requested status transition is not allowed."""


def allowed_transitions(current: IncidentStatus) -> frozenset[IncidentStatus]:
    return _ALLOWED[current]


def can_transition(current: IncidentStatus, target: IncidentStatus, *, actor: str) -> bool:
    if actor == "system" and target is IncidentStatus.RESOLVED and current.is_active:
        return True
    return target in _ALLOWED[current]


def validate_transition(current: IncidentStatus, target: IncidentStatus, *, actor: str) -> None:
    if not can_transition(current, target, actor=actor):
        raise InvalidTransitionError(
            f"cannot move incident from {current} to {target}"
            f" (allowed: {sorted(_ALLOWED[current]) or 'none - terminal'})"
        )
