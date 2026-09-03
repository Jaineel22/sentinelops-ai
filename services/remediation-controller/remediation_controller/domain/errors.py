"""Domain error types for the remediation controller.

All extend :class:`ValueError` so callers (and Pydantic validators, which wrap
raised ``ValueError`` into ``ValidationError``) can catch them uniformly — the
same convention the Phase 3 / Phase 4 state machines use
(``InvalidTransitionError(ValueError)``).
"""

from __future__ import annotations


class RemediationDomainError(ValueError):
    """Base for every remediation-domain rule violation."""


class UnknownActionError(RemediationDomainError):
    """The action type is not in the closed catalogue — fail closed."""


class UnknownTargetError(RemediationDomainError):
    """The target service is not on the remediation allow-list — fail closed."""


class ParameterValidationError(RemediationDomainError):
    """Action parameters do not satisfy the catalogue's bounded schema."""


class InvalidRemediationTransition(RemediationDomainError):
    """The requested remediation status transition is not allowed."""


class ApprovalError(RemediationDomainError):
    """An execution was requested without a valid, matching human approval."""
