"""Policy-layer errors. Subclass the shared domain base so callers can catch
``RemediationDomainError`` uniformly (same convention as the 5A domain)."""

from __future__ import annotations

from remediation_controller.domain.errors import RemediationDomainError


class PolicyError(RemediationDomainError):
    """A misuse of the policy layer (e.g. applying a decision to a proposal that
    is not in a policy-input lifecycle state). Rule *failures* are represented as
    a ``DENY`` :class:`~remediation_controller.policy.decision.PolicyDecision`,
    not as an exception."""
