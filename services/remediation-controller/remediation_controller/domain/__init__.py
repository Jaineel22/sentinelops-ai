"""Phase 5A remediation domain — the closed action catalogue, the structural
proposal model, the lifecycle state machine, the approval model, and the
deterministic RCA-recommendation -> proposal mapping.

No executor, no persistence, no API, no Kafka. See
:mod:`remediation_controller` for the Phase 5 overview.
"""

from __future__ import annotations

from remediation_controller.domain.catalogue import (
    ACTION_CATALOGUE,
    ActionDefinition,
    ActionParameter,
    ParameterValue,
    get_action_definition,
    is_allowed_target,
    is_known_action,
    require_action_definition,
    validate_action_parameters,
)
from remediation_controller.domain.enums import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    ApprovalDecision,
    ApproverRole,
    ExecutorType,
    RemediationActionType,
    RemediationStatus,
    RemediationTrigger,
    RiskLevel,
    TargetType,
)
from remediation_controller.domain.errors import (
    ApprovalError,
    InvalidRemediationTransition,
    ParameterValidationError,
    RemediationDomainError,
    UnknownActionError,
    UnknownTargetError,
)
from remediation_controller.domain.models import (
    ALLOWED_ENVIRONMENTS,
    ALLOWED_TARGET_SERVICES,
    RemediationApproval,
    ServiceTarget,
    is_allowed_service,
    new_approval_id,
    new_remediation_id,
    resolve_target,
)
from remediation_controller.domain.proposal import (
    BlockedProposal,
    MappingResult,
    RcaRecommendedActionInput,
    RemediationProposal,
    authorize_execution,
    proposal_from_rca,
)
from remediation_controller.domain.state_machine import (
    allowed_transitions,
    can_transition,
    validate_transition,
)

__all__ = [
    "ACTION_CATALOGUE",
    "ACTIVE_STATUSES",
    "ALLOWED_ENVIRONMENTS",
    "ALLOWED_TARGET_SERVICES",
    "TERMINAL_STATUSES",
    "ActionDefinition",
    "ActionParameter",
    "ApprovalDecision",
    "ApprovalError",
    "ApproverRole",
    "BlockedProposal",
    "ExecutorType",
    "InvalidRemediationTransition",
    "MappingResult",
    "ParameterValidationError",
    "ParameterValue",
    "RcaRecommendedActionInput",
    "RemediationActionType",
    "RemediationApproval",
    "RemediationDomainError",
    "RemediationProposal",
    "RemediationStatus",
    "RemediationTrigger",
    "RiskLevel",
    "ServiceTarget",
    "TargetType",
    "UnknownActionError",
    "UnknownTargetError",
    "allowed_transitions",
    "authorize_execution",
    "can_transition",
    "get_action_definition",
    "is_allowed_service",
    "is_allowed_target",
    "is_known_action",
    "new_approval_id",
    "new_remediation_id",
    "proposal_from_rca",
    "require_action_definition",
    "resolve_target",
    "validate_action_parameters",
    "validate_transition",
]
