"""Phase 5 remediation domain vocabulary — enums only.

Kept free of Pydantic / SQLAlchemy / framework imports so the catalogue, the
state machine, and the mapping stay pure and trivially unit-testable (same
discipline as ``incident_correlator.domain`` and ``rca_agent.domain``).

Every enum here is **closed**. There is deliberately no ``CUSTOM`` /
``ARBITRARY`` / ``OTHER`` member on :class:`RemediationActionType` and no
free-form escape hatch anywhere — an LLM-produced string can never widen these.
"""

from __future__ import annotations

from enum import StrEnum


class RemediationActionType(StrEnum):
    """The closed set of remediation actions this controller can ever execute.

    Deliberately small. Each member has exactly one :class:`ActionDefinition`
    in :data:`remediation_controller.domain.catalogue.ACTION_CATALOGUE`. There is
    no ``EXECUTE_COMMAND`` / ``RUN_SHELL`` / ``KUBECTL`` / ``ARBITRARY_SCRIPT``
    member — by construction, not by policy.
    """

    RESTART_SERVICE = "RESTART_SERVICE"
    SCALE_SERVICE = "SCALE_SERVICE"
    ROLL_BACK_DEPLOYMENT = "ROLL_BACK_DEPLOYMENT"
    DISABLE_FEATURE_FLAG = "DISABLE_FEATURE_FLAG"


class RemediationStatus(StrEnum):
    """Lifecycle of a single remediation, from proposal to verified recovery.

    Transitions are enforced by :mod:`remediation_controller.domain.state_machine`.
    The only path to :attr:`EXECUTING` is from :attr:`APPROVED`, so a remediation
    can never execute without a recorded human approval.
    """

    PROPOSED = "PROPOSED"
    POLICY_EVALUATION = "POLICY_EVALUATION"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    VERIFYING = "VERIFYING"
    # --- terminal ---
    BLOCKED = "BLOCKED"  # catalogue / policy rejected it; never executable
    REJECTED = "REJECTED"  # a human declined it
    EXPIRED = "EXPIRED"  # not approved before expires_at
    EXECUTION_FAILED = "EXECUTION_FAILED"
    RECOVERED = "RECOVERED"
    RECOVERY_FAILED = "RECOVERY_FAILED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL


_TERMINAL: frozenset[RemediationStatus] = frozenset(
    {
        RemediationStatus.BLOCKED,
        RemediationStatus.REJECTED,
        RemediationStatus.EXPIRED,
        RemediationStatus.EXECUTION_FAILED,
        RemediationStatus.RECOVERED,
        RemediationStatus.RECOVERY_FAILED,
    }
)

TERMINAL_STATUSES: frozenset[RemediationStatus] = _TERMINAL
ACTIVE_STATUSES: frozenset[RemediationStatus] = frozenset(
    s for s in RemediationStatus if s not in _TERMINAL
)


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

    @property
    def rank(self) -> int:
        """Ordered comparison (``StrEnum`` compares by string value, so callers
        must use ``.rank`` — mirrors ``incident_correlator.domain.Severity``)."""

        return _RISK_ORDER.index(self)


_RISK_ORDER: tuple[RiskLevel, ...] = (RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH)


class ExecutorType(StrEnum):
    """Which executor backend an action runs through.

    Only :attr:`LOCAL_SIMULATION` exists in Phase 5 — a real Kubernetes / cloud
    executor is a deliberate future item and is intentionally *not* enumerated
    here yet (fail closed: you cannot reference an executor that does not exist).
    """

    LOCAL_SIMULATION = "LOCAL_SIMULATION"


class ExecutionStatus(StrEnum):
    """Status of a single execution attempt (Phase 5D).

    Distinct from :class:`RemediationStatus` (the remediation lifecycle):
    ``SUCCEEDED`` ↔ remediation ``EXECUTED``; ``FAILED`` ↔ ``EXECUTION_FAILED``.
    """

    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class TargetType(StrEnum):
    """The kind of thing a remediation acts on. Only services, for now."""

    SERVICE = "SERVICE"


class RemediationTrigger(StrEnum):
    """What caused a remediation proposal to be created."""

    RCA_RECOMMENDATION = "RCA_RECOMMENDATION"  # deterministic mapping of a Phase 4 recommendation
    MANUAL = "MANUAL"  # an operator proposed it directly (later sub-phase)


class ApprovalDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class ApproverRole(StrEnum):
    """A small, explicit authorization vocabulary (spec section 11).

    Which role may approve which action is a Sub-phase 5C concern; Phase 5A only
    records the role on an approval and rejects an absent approver identity.
    """

    OPERATOR = "OPERATOR"
    INCIDENT_RESPONDER = "INCIDENT_RESPONDER"
    ADMINISTRATOR = "ADMINISTRATOR"
