"""Investigation lifecycle state machine (ADR-019).

Explicit adjacency, no arbitrary transitions. The engine (Sub-phase 4C) is the
only caller; there is no per-actor rule (unlike the incident state machine),
because an investigation is driven entirely by the agent runtime.

    PENDING              -> PLANNING | FAILED
    PLANNING             -> COLLECTING_EVIDENCE | INSUFFICIENT_EVIDENCE | FAILED | TIMED_OUT
    COLLECTING_EVIDENCE  -> COLLECTING_EVIDENCE | ANALYZING | INSUFFICIENT_EVIDENCE
                            | FAILED | TIMED_OUT
    ANALYZING            -> ANALYZING | VERIFYING | INSUFFICIENT_EVIDENCE | FAILED | TIMED_OUT
    VERIFYING            -> ANALYZING | COMPLETED | INSUFFICIENT_EVIDENCE | FAILED | TIMED_OUT
    COMPLETED / INSUFFICIENT_EVIDENCE / FAILED / TIMED_OUT -> (terminal)

Self-loops on ``COLLECTING_EVIDENCE`` and ``ANALYZING`` model the *bounded* tool
loop and a single re-analysis pass; the loop bounds live in
:mod:`rca_agent.limits`, not here.
"""

from __future__ import annotations

from rca_agent.domain import InvestigationStatus

S = InvestigationStatus

_ALLOWED: dict[S, frozenset[S]] = {
    S.PENDING: frozenset({S.PLANNING, S.FAILED}),
    S.PLANNING: frozenset({S.COLLECTING_EVIDENCE, S.INSUFFICIENT_EVIDENCE, S.FAILED, S.TIMED_OUT}),
    S.COLLECTING_EVIDENCE: frozenset(
        {
            S.COLLECTING_EVIDENCE,
            S.ANALYZING,
            S.INSUFFICIENT_EVIDENCE,
            S.FAILED,
            S.TIMED_OUT,
        }
    ),
    S.ANALYZING: frozenset(
        {S.ANALYZING, S.VERIFYING, S.INSUFFICIENT_EVIDENCE, S.FAILED, S.TIMED_OUT}
    ),
    S.VERIFYING: frozenset(
        {S.ANALYZING, S.COMPLETED, S.INSUFFICIENT_EVIDENCE, S.FAILED, S.TIMED_OUT}
    ),
    S.COMPLETED: frozenset(),
    S.INSUFFICIENT_EVIDENCE: frozenset(),
    S.FAILED: frozenset(),
    S.TIMED_OUT: frozenset(),
}


class InvalidInvestigationTransition(ValueError):
    """The requested investigation status transition is not allowed."""


def allowed_transitions(current: InvestigationStatus) -> frozenset[InvestigationStatus]:
    return _ALLOWED[current]


def can_transition(current: InvestigationStatus, target: InvestigationStatus) -> bool:
    return target in _ALLOWED[current]


def validate_transition(current: InvestigationStatus, target: InvestigationStatus) -> None:
    if not can_transition(current, target):
        allowed = sorted(_ALLOWED[current]) or "none - terminal"
        raise InvalidInvestigationTransition(
            f"cannot move investigation from {current} to {target} (allowed: {allowed})"
        )
