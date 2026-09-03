"""The deterministic policy rules.

Each rule is a pure function ``(proposal, context, config) -> list[PolicyViolation]``
— an empty list means the rule passed. Rules never mutate anything, never raise
for a *policy* failure (they return a violation), never call an LLM, and never
read ``proposal.reason`` / ``proposal.expected_effect`` / any free-text field.

The rule set and its order are fixed (see :data:`RULES`). :class:`PolicyEngine`
runs **all** of them and collects **every** violation so multiple failures are
reported deterministically.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from remediation_controller.domain.catalogue import (
    get_action_definition,
    validate_action_parameters,
)
from remediation_controller.domain.enums import RemediationActionType, RemediationStatus
from remediation_controller.domain.errors import ParameterValidationError, UnknownActionError
from remediation_controller.domain.models import is_allowed_service
from remediation_controller.domain.proposal import RemediationProposal
from remediation_controller.policy.codes import PolicyReasonCode
from remediation_controller.policy.context import KNOWN_SEVERITIES, PolicyConfig, PolicyContext
from remediation_controller.policy.decision import PolicyViolation

Rule = Callable[[RemediationProposal, PolicyContext, PolicyConfig], list[PolicyViolation]]

# Policy evaluation only accepts a proposal that has not yet passed the human
# gate. Every other status — terminal or already past approval — fails closed.
POLICY_INPUT_STATES: frozenset[RemediationStatus] = frozenset(
    {RemediationStatus.PROPOSED, RemediationStatus.POLICY_EVALUATION}
)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _v(code: PolicyReasonCode, rule: str, detail: str) -> PolicyViolation:
    return PolicyViolation(code=code, rule=rule, detail=detail[:500])


# --- 1. lifecycle state ------------------------------------------------
def state_rule(
    proposal: RemediationProposal, ctx: PolicyContext, config: PolicyConfig
) -> list[PolicyViolation]:
    if proposal.status not in POLICY_INPUT_STATES:
        return [
            _v(
                PolicyReasonCode.INVALID_STATE,
                "state",
                f"proposal is {proposal.status}; policy evaluation only accepts "
                f"{sorted(s.value for s in POLICY_INPUT_STATES)}",
            )
        ]
    return []


# --- 2. action eligibility ------------------------------------------
def action_rule(
    proposal: RemediationProposal, ctx: PolicyContext, config: PolicyConfig
) -> list[PolicyViolation]:
    definition = get_action_definition(proposal.action_type)
    if definition is None:
        return [
            _v(
                PolicyReasonCode.ACTION_NOT_ALLOWED,
                "action",
                f"action {proposal.action_type!r} is not in the closed catalogue",
            )
        ]
    if proposal.action_type not in config.eligible_actions:
        return [
            _v(
                PolicyReasonCode.ACTION_NOT_ALLOWED,
                "action",
                f"action {proposal.action_type} is disabled by policy v{config.policy_version}",
            )
        ]
    return []


# --- 3. target eligibility -----------------------------------------
def target_rule(
    proposal: RemediationProposal, ctx: PolicyContext, config: PolicyConfig
) -> list[PolicyViolation]:
    definition = get_action_definition(proposal.action_type)
    if definition is None:
        return []  # action_rule owns this failure
    target = proposal.target
    if not is_allowed_service(target.service_name):
        return [
            _v(
                PolicyReasonCode.TARGET_NOT_ALLOWED,
                "target",
                f"service {target.service_name!r} is not on the remediation allow-list",
            )
        ]
    if target.service_name not in definition.allowed_target_services:
        return [
            _v(
                PolicyReasonCode.TARGET_NOT_ALLOWED,
                "target",
                f"service {target.service_name!r} is not allowed for action {proposal.action_type}",
            )
        ]
    if target.target_type not in definition.allowed_target_types:
        return [
            _v(
                PolicyReasonCode.TARGET_NOT_ALLOWED,
                "target",
                f"target type {target.target_type} is not allowed for {proposal.action_type}",
            )
        ]
    return []


# --- 4. environment ------------------------------------------------
def environment_rule(
    proposal: RemediationProposal, ctx: PolicyContext, config: PolicyConfig
) -> list[PolicyViolation]:
    env = proposal.target.environment
    if env not in config.allowed_environments:
        return [
            _v(
                PolicyReasonCode.ENVIRONMENT_NOT_ALLOWED,
                "environment",
                f"environment {env!r} is not policy-allowed "
                f"({sorted(config.allowed_environments)})",
            )
        ]
    return []


# --- 5. severity --------------------------------------------------
def severity_rule(
    proposal: RemediationProposal, ctx: PolicyContext, config: PolicyConfig
) -> list[PolicyViolation]:
    definition = get_action_definition(proposal.action_type)
    if definition is None:
        return []
    severity = ctx.incident_severity
    if severity is None:
        if config.require_known_incident_severity:
            return [
                _v(
                    PolicyReasonCode.SEVERITY_NOT_ALLOWED,
                    "severity",
                    "incident severity could not be verified; failing closed",
                )
            ]
        return []
    if severity not in KNOWN_SEVERITIES:
        return [
            _v(
                PolicyReasonCode.SEVERITY_NOT_ALLOWED,
                "severity",
                f"unrecognised incident severity {severity!r}",
            )
        ]
    if severity not in definition.allowed_severities:
        return [
            _v(
                PolicyReasonCode.SEVERITY_NOT_ALLOWED,
                "severity",
                f"severity {severity} is not eligible for action {proposal.action_type} "
                f"(allowed: {sorted(definition.allowed_severities)})",
            )
        ]
    return []


# --- 6. parameters -----------------------------------------------
def parameter_rule(
    proposal: RemediationProposal, ctx: PolicyContext, config: PolicyConfig
) -> list[PolicyViolation]:
    try:
        validate_action_parameters(proposal.action_type, proposal.parameters)
    except UnknownActionError:
        return []  # action_rule owns this failure
    except ParameterValidationError as exc:
        return [_v(PolicyReasonCode.PARAMETER_INVALID, "parameters", str(exc))]
    return []


# --- 7. risk / blast radius -------------------------------------
def risk_rule(
    proposal: RemediationProposal, ctx: PolicyContext, config: PolicyConfig
) -> list[PolicyViolation]:
    definition = get_action_definition(proposal.action_type)
    if definition is None:
        return []
    out: list[PolicyViolation] = []

    # Risk is taken from the catalogue definition only, never from the
    # proposal's own declared risk field (which an LLM-derived mapping set).
    if definition.risk_level.rank > config.max_allowed_risk.rank:
        out.append(
            _v(
                PolicyReasonCode.RISK_EXCEEDED,
                "risk",
                f"action risk {definition.risk_level} exceeds the policy ceiling "
                f"{config.max_allowed_risk}",
            )
        )

    effective_blast_radius = definition.max_blast_radius
    if proposal.action_type is RemediationActionType.SCALE_SERVICE:
        replicas = proposal.parameters.get("replicas")
        if isinstance(replicas, int) and not isinstance(replicas, bool):
            effective_blast_radius = max(effective_blast_radius, replicas)

    if effective_blast_radius > config.max_blast_radius:
        out.append(
            _v(
                PolicyReasonCode.RISK_EXCEEDED,
                "risk",
                f"effective blast radius {effective_blast_radius} exceeds the policy limit "
                f"{config.max_blast_radius}",
            )
        )
    return out


# --- 8. expiry --------------------------------------------------
def expiry_rule(
    proposal: RemediationProposal, ctx: PolicyContext, config: PolicyConfig
) -> list[PolicyViolation]:
    created = _aware(proposal.created_at)
    expires = _aware(proposal.expires_at)
    now = _aware(ctx.now)
    if expires <= created:
        return [
            _v(
                PolicyReasonCode.PROPOSAL_EXPIRED,
                "expiry",
                "proposal approval window is malformed (expires_at <= created_at)",
            )
        ]
    if now >= expires:
        return [
            _v(
                PolicyReasonCode.PROPOSAL_EXPIRED,
                "expiry",
                f"proposal expired at {expires.isoformat()}",
            )
        ]
    return []


# --- 9. cooldown / duplicate ----------------------------------
def cooldown_rule(
    proposal: RemediationProposal, ctx: PolicyContext, config: PolicyConfig
) -> list[PolicyViolation]:
    definition = get_action_definition(proposal.action_type)
    if definition is None:
        return []
    out: list[PolicyViolation] = []
    if ctx.history.active_remediation_exists(
        incident_id=proposal.incident_id,
        action_type=proposal.action_type,
        target=proposal.target,
    ):
        out.append(
            _v(
                PolicyReasonCode.DUPLICATE_ACTIVE,
                "cooldown",
                "another remediation for this incident/action/target is already in flight",
            )
        )
    last_completed = ctx.history.last_completed_at(
        incident_id=proposal.incident_id,
        action_type=proposal.action_type,
        target=proposal.target,
    )
    if last_completed is not None:
        elapsed = (_aware(ctx.now) - _aware(last_completed)).total_seconds()
        if elapsed < definition.cooldown_seconds:
            out.append(
                _v(
                    PolicyReasonCode.COOLDOWN_ACTIVE,
                    "cooldown",
                    f"only {elapsed:.0f}s since the last completed remediation; "
                    f"cooldown is {definition.cooldown_seconds}s",
                )
            )
    return out


RULES: tuple[Rule, ...] = (
    state_rule,
    action_rule,
    target_rule,
    environment_rule,
    severity_rule,
    parameter_rule,
    risk_rule,
    expiry_rule,
    cooldown_rule,
)
