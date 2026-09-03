"""Remediation lifecycle state machine (spec section 6).

Explicit adjacency, no arbitrary transitions, fail closed — the same shape as
``incident_correlator.state_machine`` and ``rca_agent.state_machine``.

    PROPOSED           -> POLICY_EVALUATION | BLOCKED
    POLICY_EVALUATION  -> PENDING_APPROVAL | BLOCKED
    PENDING_APPROVAL   -> APPROVED | REJECTED | EXPIRED
    APPROVED           -> EXECUTING | EXPIRED
    EXECUTING          -> EXECUTED | EXECUTION_FAILED
    EXECUTED           -> VERIFYING
    VERIFYING          -> RECOVERED | RECOVERY_FAILED
    BLOCKED | REJECTED | EXPIRED | EXECUTION_FAILED | RECOVERED | RECOVERY_FAILED -> (terminal)

The **only** edge into ``EXECUTING`` is ``APPROVED -> EXECUTING``: a remediation
can never execute without first being approved. Phase 5A does not execute
anything — this only fixes the lifecycle contract that later sub-phases enforce.
"""

from __future__ import annotations

from remediation_controller.domain.enums import RemediationStatus
from remediation_controller.domain.errors import InvalidRemediationTransition

S = RemediationStatus

_ALLOWED: dict[S, frozenset[S]] = {
    S.PROPOSED: frozenset({S.POLICY_EVALUATION, S.BLOCKED}),
    S.POLICY_EVALUATION: frozenset({S.PENDING_APPROVAL, S.BLOCKED}),
    S.PENDING_APPROVAL: frozenset({S.APPROVED, S.REJECTED, S.EXPIRED}),
    S.APPROVED: frozenset({S.EXECUTING, S.EXPIRED}),
    S.EXECUTING: frozenset({S.EXECUTED, S.EXECUTION_FAILED}),
    S.EXECUTED: frozenset({S.VERIFYING}),
    S.VERIFYING: frozenset({S.RECOVERED, S.RECOVERY_FAILED}),
    S.BLOCKED: frozenset(),
    S.REJECTED: frozenset(),
    S.EXPIRED: frozenset(),
    S.EXECUTION_FAILED: frozenset(),
    S.RECOVERED: frozenset(),
    S.RECOVERY_FAILED: frozenset(),
}

# The single predecessor set for EXECUTING — asserted here so an edit that lets
# some other state reach EXECUTING fails loudly.
_EXECUTING_PREDECESSORS = frozenset(s for s, nexts in _ALLOWED.items() if S.EXECUTING in nexts)
assert frozenset({S.APPROVED}) == _EXECUTING_PREDECESSORS, (
    "EXECUTING must be reachable only from APPROVED"
)

# Phase 5F guards: recovery verification can never bypass execution. VERIFYING is
# reachable only from EXECUTED, and the terminal recovery verdicts only from
# VERIFYING — so there is no APPROVED/EXECUTING -> RECOVERED shortcut.
_VERIFYING_PREDECESSORS = frozenset(s for s, nexts in _ALLOWED.items() if S.VERIFYING in nexts)
assert frozenset({S.EXECUTED}) == _VERIFYING_PREDECESSORS, (
    "VERIFYING must be reachable only from EXECUTED"
)

_RECOVERY_PREDECESSORS = frozenset(
    s for s, nexts in _ALLOWED.items() if nexts & {S.RECOVERED, S.RECOVERY_FAILED}
)
assert frozenset({S.VERIFYING}) == _RECOVERY_PREDECESSORS, (
    "RECOVERED / RECOVERY_FAILED must be reachable only from VERIFYING"
)


def allowed_transitions(current: RemediationStatus) -> frozenset[RemediationStatus]:
    return _ALLOWED[current]


def can_transition(current: RemediationStatus, target: RemediationStatus) -> bool:
    return target in _ALLOWED[current]


def validate_transition(current: RemediationStatus, target: RemediationStatus) -> None:
    if not can_transition(current, target):
        allowed = sorted(_ALLOWED[current]) or "none - terminal"
        raise InvalidRemediationTransition(
            f"cannot move remediation from {current} to {target} (allowed: {allowed})"
        )
