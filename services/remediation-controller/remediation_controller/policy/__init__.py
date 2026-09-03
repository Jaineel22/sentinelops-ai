"""Phase 5B — deterministic remediation policy validation.

Independently validates whether an already-created
:class:`~remediation_controller.domain.proposal.RemediationProposal` is eligible
to advance to human approval. Completely deterministic; **no LLM**; the RCA
agent's risk assessment and prose are never trusted.

    PolicyEngine(config).evaluate(proposal, context) -> PolicyDecision
    apply_policy_decision(proposal, decision) -> RemediationProposal
        # ALLOW -> PENDING_APPROVAL, DENY -> BLOCKED (never EXECUTING/EXECUTED)

No persistence, API, Kafka, or executor here — those are Sub-phases 5C+. Prior
remediation history (for cooldown / duplicate checks) is read through the
injectable :class:`RemediationHistoryPort`, which 5C backs with PostgreSQL.
"""

from __future__ import annotations

from remediation_controller.policy.codes import (
    POLICY_VERSION,
    PolicyOutcome,
    PolicyReasonCode,
)
from remediation_controller.policy.context import (
    KNOWN_SEVERITIES,
    NullRemediationHistory,
    PolicyConfig,
    PolicyContext,
    RemediationHistoryEntry,
    RemediationHistoryPort,
)
from remediation_controller.policy.decision import PolicyDecision, PolicyViolation
from remediation_controller.policy.engine import PolicyEngine, apply_policy_decision
from remediation_controller.policy.errors import PolicyError
from remediation_controller.policy.rules import POLICY_INPUT_STATES, RULES

__all__ = [
    "KNOWN_SEVERITIES",
    "POLICY_INPUT_STATES",
    "POLICY_VERSION",
    "RULES",
    "NullRemediationHistory",
    "PolicyConfig",
    "PolicyContext",
    "PolicyDecision",
    "PolicyEngine",
    "PolicyError",
    "PolicyOutcome",
    "PolicyReasonCode",
    "PolicyViolation",
    "RemediationHistoryEntry",
    "RemediationHistoryPort",
    "apply_policy_decision",
]
