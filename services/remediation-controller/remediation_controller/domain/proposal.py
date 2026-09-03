"""The remediation proposal model and the deterministic RCA -> proposal mapping.

A :class:`RemediationProposal` represents **intent**, not an execution
instruction. It has:

* a closed-enum ``action_type`` (never a string),
* a structured, allow-listed ``target`` (never a free string),
* ``parameters`` restricted to catalogue-defined, bounded, named values,
* ``requires_approval`` typed ``Literal[True]``,
* ``model_config = extra="forbid"`` — so a caller cannot bolt on a ``command`` /
  ``script`` / ``shell`` field.

There is no field anywhere on this model that can hold a command.

:func:`proposal_from_rca` is the *only* way an AI recommendation becomes a
proposal. It is deterministic, total, and fail-closed: a recommendation category
outside the closed catalogue, or one naming a non-allow-listed target, yields a
:class:`BlockedProposal` (terminal ``BLOCKED``) — never a silent executable.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from remediation_controller.domain.catalogue import (
    ParameterValue,
    get_action_definition,
    is_allowed_target,
    require_action_definition,
    validate_action_parameters,
)
from remediation_controller.domain.enums import (
    ApprovalDecision,
    RemediationActionType,
    RemediationStatus,
    RemediationTrigger,
    RiskLevel,
)
from remediation_controller.domain.errors import ApprovalError, RemediationDomainError
from remediation_controller.domain.models import (
    INCIDENT_ID_RE,
    INVESTIGATION_ID_RE,
    REMEDIATION_ID_RE,
    RemediationApproval,
    ServiceTarget,
    new_remediation_id,
)

_DEFAULT_TTL = timedelta(hours=1)

# Deterministic map from a Phase 4 ``RecommendedAction.action_type`` *label* to a
# Phase 5 catalogue action. Keyed by string so an unknown / new / adversarial
# Phase 4 category simply misses and is BLOCKED (fail closed) — we never trust
# the label to be a known enum member.
#
# Phase 4 categories deliberately NOT mapped (each -> BLOCKED, a human decides):
#   INVESTIGATE_FURTHER, MONITOR, NO_ACTION_NEEDED, MANUAL_REVIEW_REQUIRED
#       -> not an operational change at all
#   ADJUST_CONFIGURATION, FAILOVER_DEPENDENCY, CONTACT_SERVICE_OWNER
#       -> too broad / no safe bounded catalogue action to represent them
_RCA_ACTION_MAP: dict[str, RemediationActionType] = {
    "RESTART_SERVICE": RemediationActionType.RESTART_SERVICE,
    "ROLL_BACK_DEPLOYMENT": RemediationActionType.ROLL_BACK_DEPLOYMENT,
    "SCALE_SERVICE": RemediationActionType.SCALE_SERVICE,
}

_LABEL_SAFE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def _safe_label(raw: str) -> str:
    """Render an externally-supplied category label into an error message without
    letting it carry surprises. Not security-critical (the label is never
    executed), just tidy."""

    return raw if _LABEL_SAFE.match(raw) else "<invalid-label>"


class RcaRecommendedActionInput(BaseModel):
    """The slice of a Phase 4 ``RecommendedAction`` that Phase 5 consumes.

    Re-declared here (``extra="ignore"``) rather than imported from ``rca_agent``
    — the same pattern Phase 4 uses for the Incident API payloads (ADR-020). Feed
    it ``RecommendedAction.model_dump()``.

    ``action_type`` is intentionally a bounded ``str``, not the Phase 4 enum: the
    mapping must treat an unrecognised label as BLOCKED, not raise.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    action_type: str = Field(min_length=1, max_length=64)
    target_service: str | None = Field(default=None, max_length=128)
    description: str = Field(default="", max_length=4000)
    rationale: str = Field(default="", max_length=4000)
    evidence_ids: tuple[str, ...] = ()


class RemediationProposal(BaseModel):
    """A strongly typed, immutable remediation *intent*.

    Constructing one validates (via :meth:`_check`) that the action is in the
    closed catalogue, the target is allow-listed for that action, the parameters
    satisfy the catalogue schema, and the expiry is after creation. An invalid
    action, target, or parameter set cannot produce a proposal.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    remediation_id: str = Field(pattern=REMEDIATION_ID_RE)
    incident_id: str = Field(pattern=INCIDENT_ID_RE)
    investigation_id: str | None = Field(default=None, pattern=INVESTIGATION_ID_RE)
    trigger: RemediationTrigger
    proposed_by: str = Field(min_length=1, max_length=128)

    action_type: RemediationActionType
    target: ServiceTarget
    parameters: dict[str, ParameterValue] = Field(default_factory=dict)

    risk_level: RiskLevel
    requires_approval: Literal[True] = True

    # Traceability back to the AI recommendation. ``reason`` / ``expected_effect``
    # are explanatory prose for the human approver — plain data, never parsed.
    source_recommendation: str = Field(default="", max_length=64)
    reason: str = Field(default="", max_length=4000)
    expected_effect: str = Field(default="", max_length=2000)
    evidence_references: tuple[str, ...] = ()

    status: RemediationStatus = RemediationStatus.PROPOSED
    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def _check(self) -> RemediationProposal:
        if get_action_definition(self.action_type) is None:  # pragma: no cover - enum guards this
            raise ValueError(f"action {self.action_type!r} is not in the closed catalogue")
        if not is_allowed_target(self.action_type, self.target):
            raise ValueError(
                f"target {self.target} is not allow-listed for action {self.action_type}"
            )
        # Raises ParameterValidationError (a ValueError) on any schema violation.
        validate_action_parameters(self.action_type, self.parameters)
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        return self

    @property
    def is_executable_candidate(self) -> bool:
        """A proposal is only ever a *candidate*: it still needs policy + a human."""

        return self.status not in {RemediationStatus.BLOCKED}


class BlockedProposal(BaseModel):
    """The fail-closed result of :func:`proposal_from_rca` when a recommendation
    cannot become an executable proposal. Terminal by construction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    remediation_id: str = Field(pattern=REMEDIATION_ID_RE)
    incident_id: str = Field(pattern=INCIDENT_ID_RE)
    investigation_id: str | None = Field(default=None, pattern=INVESTIGATION_ID_RE)
    source_recommendation: str = Field(max_length=64)
    mapped_action_type: RemediationActionType | None = None
    block_reason: str = Field(max_length=500)
    status: Literal[RemediationStatus.BLOCKED] = RemediationStatus.BLOCKED
    created_at: datetime


MappingResult = RemediationProposal | BlockedProposal


def proposal_from_rca(
    recommendation: RcaRecommendedActionInput,
    *,
    incident_id: str,
    investigation_id: str | None = None,
    incident_severity: str | None = None,
    target_environment: str = "development",
    proposed_by: str = "rca-agent",
    now: datetime | None = None,
    ttl: timedelta = _DEFAULT_TTL,
) -> MappingResult:
    """Deterministically convert a Phase 4 recommendation into a Phase 5 result.

    Returns a :class:`RemediationProposal` (status ``PROPOSED``) **only** when the
    recommendation maps onto a closed-catalogue action against an allow-listed
    target with auto-satisfiable parameters. Every other case — an unmapped
    category, a missing/blocked target, a required parameter that needs a human,
    an ineligible severity — returns a :class:`BlockedProposal`. Never raises on
    hostile input.
    """

    ts = now or datetime.now(tz=UTC)
    rid = new_remediation_id()
    label = _safe_label(recommendation.action_type)

    def blocked(reason: str, mapped: RemediationActionType | None = None) -> BlockedProposal:
        return BlockedProposal(
            remediation_id=rid,
            incident_id=incident_id,
            investigation_id=investigation_id,
            source_recommendation=label,
            mapped_action_type=mapped,
            block_reason=reason,
            created_at=ts,
        )

    action_type = _RCA_ACTION_MAP.get(recommendation.action_type)
    if action_type is None:
        return blocked(
            f"RCA recommendation category {label!r} has no executable action in the "
            f"closed remediation catalogue; a human must decide"
        )

    definition = require_action_definition(action_type)

    if incident_severity is not None and incident_severity not in definition.allowed_severities:
        return blocked(
            f"incident severity {_safe_label(incident_severity)!r} is not eligible for "
            f"action {action_type}",
            mapped=action_type,
        )

    if definition.required_parameter_names():
        return blocked(
            f"action {action_type} requires operator-supplied parameter(s) "
            f"{sorted(definition.required_parameter_names())} and cannot be derived "
            f"automatically from an RCA recommendation",
            mapped=action_type,
        )

    if not recommendation.target_service:
        return blocked("RCA recommendation did not name a target service", mapped=action_type)

    try:
        target = ServiceTarget(
            service_name=recommendation.target_service, environment=target_environment
        )
    except ValueError:
        return blocked(
            f"RCA target service {_safe_label(recommendation.target_service)!r} is not a "
            f"valid service target",
            mapped=action_type,
        )

    if not is_allowed_target(action_type, target):
        return blocked(
            f"target service {target.service_name!r} is not on the remediation allow-list "
            f"for action {action_type}",
            mapped=action_type,
        )

    try:
        return RemediationProposal(
            remediation_id=rid,
            incident_id=incident_id,
            investigation_id=investigation_id,
            trigger=RemediationTrigger.RCA_RECOMMENDATION,
            proposed_by=proposed_by,
            action_type=action_type,
            target=target,
            parameters={},
            risk_level=definition.risk_level,
            source_recommendation=label,
            reason=(recommendation.rationale.strip() or recommendation.description.strip())[:4000]
            or "Derived from a Phase 4 RCA recommendation.",
            expected_effect=definition.description,
            evidence_references=recommendation.evidence_ids,
            created_at=ts,
            expires_at=ts + ttl,
        )
    except (RemediationDomainError, ValidationError) as exc:  # pragma: no cover - guards cover this
        return blocked(
            f"proposal construction failed catalogue/target validation ({type(exc).__name__})",
            mapped=action_type,
        )


def authorize_execution(
    proposal: RemediationProposal, approval: RemediationApproval | None
) -> None:
    """Fail-closed guard: raise :class:`ApprovalError` unless ``proposal`` is
    ``APPROVED`` and ``approval`` is a matching, affirmative human decision.

    Phase 5A does not execute anything; this is the contract the Sub-phase 5D
    executor will call before it does. It cannot be bypassed — there is no code
    path to ``EXECUTING`` that does not go through ``APPROVED``, and no way to
    reach ``APPROVED`` without a recorded approval.
    """

    if proposal.status is not RemediationStatus.APPROVED:
        raise ApprovalError(
            f"remediation {proposal.remediation_id} is {proposal.status}, not APPROVED"
        )
    if approval is None:
        raise ApprovalError(f"remediation {proposal.remediation_id} has no recorded approval")
    if approval.remediation_id != proposal.remediation_id:
        raise ApprovalError(f"approval {approval.approval_id} is for a different remediation")
    if approval.decision is not ApprovalDecision.APPROVE:
        raise ApprovalError(
            f"approval {approval.approval_id} is a {approval.decision}, not an APPROVE"
        )
