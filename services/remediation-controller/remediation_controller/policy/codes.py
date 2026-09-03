"""Policy outcome + reason-code vocabulary and the policy version.

Deterministic, machine-readable, and free of natural language: a policy decision
records *which* concise operational codes fired, never an LLM-generated sentence.
"""

from __future__ import annotations

from enum import StrEnum

# Bump on ANY change to the rule set or its thresholds so a persisted / emitted
# decision can be tied to the exact policy that produced it. Kept intentionally
# simple (spec: "Do not over-engineer version management yet").
POLICY_VERSION = "1"


class PolicyOutcome(StrEnum):
    """The two possible results of policy evaluation.

    ``ALLOW`` means *approved for human review* — the proposal may advance to
    ``PENDING_APPROVAL``. It is **never** an execution authority: an explicit
    human approval is still mandatory afterwards (:data:`PolicyReasonCode.APPROVAL_REQUIRED`
    is always present on an ``ALLOW``).
    """

    ALLOW = "ALLOW"
    DENY = "DENY"


class PolicyReasonCode(StrEnum):
    """Concise operational reason codes. Ordered comparison is by value string."""

    # --- ALLOW-side ---
    POLICY_OK = "POLICY_OK"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    # --- DENY-side ---
    ACTION_NOT_ALLOWED = "ACTION_NOT_ALLOWED"
    TARGET_NOT_ALLOWED = "TARGET_NOT_ALLOWED"
    ENVIRONMENT_NOT_ALLOWED = "ENVIRONMENT_NOT_ALLOWED"
    SEVERITY_NOT_ALLOWED = "SEVERITY_NOT_ALLOWED"
    PARAMETER_INVALID = "PARAMETER_INVALID"
    RISK_EXCEEDED = "RISK_EXCEEDED"
    PROPOSAL_EXPIRED = "PROPOSAL_EXPIRED"
    INVALID_STATE = "INVALID_STATE"
    COOLDOWN_ACTIVE = "COOLDOWN_ACTIVE"
    DUPLICATE_ACTIVE = "DUPLICATE_ACTIVE"
