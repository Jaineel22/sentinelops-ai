"""The closed remediation action catalogue (spec sections 1, 2).

:data:`ACTION_CATALOGUE` is the **authoritative, code-defined** source of every
action this controller can ever execute. It is:

* **closed** — one entry per :class:`RemediationActionType`, no more;
* **immutable at runtime** — exposed as a ``MappingProxyType``;
* **not configurable** — no environment variable or input can add an entry.

Every entry has ``requires_approval`` typed ``Literal[True]``: it is structurally
impossible to define an executable action that skips human approval.

There is no ``EXECUTE_COMMAND`` / ``RUN_SHELL`` / ``ARBITRARY_SCRIPT`` entry and
no field anywhere that holds a command string — an action is a *type* plus a
small set of bounded, named parameters, nothing else.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from remediation_controller.domain.enums import (
    ExecutorType,
    RemediationActionType,
    RiskLevel,
    TargetType,
)
from remediation_controller.domain.errors import ParameterValidationError, UnknownActionError
from remediation_controller.domain.models import ServiceTarget, is_allowed_service

# Incident severities (mirrors ``incident_correlator.domain.Severity`` names —
# re-declared, not imported, to keep the service boundary clean).
_SEVERITIES = frozenset({"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"})

ParameterValue = str | int | bool
"""The only value types an action parameter may hold. No nested dicts/lists — a
parameter cannot carry a structure that smuggles instructions."""


class ActionParameter(BaseModel):
    """A single bounded, named parameter for an action.

    Constraints are enforced by :func:`validate_action_parameters` *before* any
    value is accepted onto a proposal. A string parameter always has an explicit
    ``pattern``; there is no unbounded free-text parameter.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    description: str = Field(max_length=300)
    required: bool
    value_type: Literal["int", "string"]
    min_value: int | None = None
    max_value: int | None = None
    pattern: str | None = None  # required for string parameters
    allowed_values: tuple[str, ...] | None = None


class ActionDefinition(BaseModel):
    """The full, immutable definition of one catalogue action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action_type: RemediationActionType
    description: str = Field(max_length=500)
    allowed_target_types: frozenset[TargetType]
    allowed_target_services: frozenset[str]
    allowed_severities: frozenset[str]
    requires_approval: Literal[True] = True
    risk_level: RiskLevel
    executor_type: ExecutorType
    max_blast_radius: int = Field(ge=1, le=50)
    timeout_seconds: int = Field(ge=1, le=1800)
    cooldown_seconds: int = Field(ge=0, le=86_400)
    parameters: tuple[ActionParameter, ...] = ()

    def required_parameter_names(self) -> frozenset[str]:
        return frozenset(p.name for p in self.parameters if p.required)


def _def(
    action_type: RemediationActionType,
    *,
    description: str,
    risk_level: RiskLevel,
    allowed_severities: frozenset[str],
    max_blast_radius: int,
    timeout_seconds: int,
    cooldown_seconds: int,
    parameters: tuple[ActionParameter, ...] = (),
) -> ActionDefinition:
    return ActionDefinition(
        action_type=action_type,
        description=description,
        allowed_target_types=frozenset({TargetType.SERVICE}),
        allowed_target_services=ALLOWED_ACTION_TARGET_SERVICES,
        allowed_severities=allowed_severities,
        risk_level=risk_level,
        executor_type=ExecutorType.LOCAL_SIMULATION,
        max_blast_radius=max_blast_radius,
        timeout_seconds=timeout_seconds,
        cooldown_seconds=cooldown_seconds,
        parameters=parameters,
    )


# Only ``orders-service`` is instrumented in this repository, so it is the only
# service any action may target (spec section 3). Kept as its own constant so
# the two allow-lists (target model + catalogue) are visibly the same decision.
ALLOWED_ACTION_TARGET_SERVICES: frozenset[str] = frozenset({"orders-service"})

_HIGHER_SEVERITIES = frozenset({"MEDIUM", "HIGH", "CRITICAL"})

_CATALOGUE: dict[RemediationActionType, ActionDefinition] = {
    RemediationActionType.RESTART_SERVICE: _def(
        RemediationActionType.RESTART_SERVICE,
        description=(
            "Restart the target service's running instances to clear transient in-process "
            "state (saturated connection pools, stuck worker threads, corrupt in-memory caches)."
        ),
        risk_level=RiskLevel.MEDIUM,
        allowed_severities=_HIGHER_SEVERITIES,
        max_blast_radius=1,
        timeout_seconds=120,
        cooldown_seconds=300,
    ),
    RemediationActionType.SCALE_SERVICE: _def(
        RemediationActionType.SCALE_SERVICE,
        description="Set the number of running replicas for the target service, within bounds.",
        risk_level=RiskLevel.MEDIUM,
        allowed_severities=_HIGHER_SEVERITIES,
        max_blast_radius=10,
        timeout_seconds=180,
        cooldown_seconds=300,
        parameters=(
            ActionParameter(
                name="replicas",
                description="Desired replica count.",
                required=True,
                value_type="int",
                min_value=1,
                max_value=10,
            ),
        ),
    ),
    RemediationActionType.ROLL_BACK_DEPLOYMENT: _def(
        RemediationActionType.ROLL_BACK_DEPLOYMENT,
        description=(
            "Roll the target service back to its previous known-good deployment revision."
        ),
        risk_level=RiskLevel.HIGH,
        allowed_severities=_HIGHER_SEVERITIES,
        max_blast_radius=1,
        timeout_seconds=300,
        cooldown_seconds=600,
        parameters=(
            ActionParameter(
                name="to_revision",
                description=(
                    "Explicit revision id to roll back to; omitted means the immediately "
                    "previous revision."
                ),
                required=False,
                value_type="string",
                pattern=r"^[A-Za-z0-9._-]{1,64}$",
            ),
        ),
    ),
    RemediationActionType.DISABLE_FEATURE_FLAG: _def(
        RemediationActionType.DISABLE_FEATURE_FLAG,
        description="Turn a named feature flag off for the target service.",
        risk_level=RiskLevel.LOW,
        allowed_severities=frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"}),
        max_blast_radius=1,
        timeout_seconds=30,
        cooldown_seconds=60,
        parameters=(
            ActionParameter(
                name="flag_key",
                description="The feature-flag key to disable.",
                required=True,
                value_type="string",
                pattern=r"^[a-z0-9_.-]{1,64}$",
            ),
        ),
    ),
}

# The public, read-only view. Mutating it (or its entries) raises at runtime.
ACTION_CATALOGUE: Mapping[RemediationActionType, ActionDefinition] = MappingProxyType(_CATALOGUE)

# Every catalogue action must exist for every action type, and vice versa — the
# catalogue is closed and total. (Checked here so an incomplete edit fails fast.)
assert set(ACTION_CATALOGUE) == set(RemediationActionType), "catalogue must be total and closed"
assert all(d.requires_approval is True for d in ACTION_CATALOGUE.values())
assert all(d.allowed_severities <= _SEVERITIES for d in ACTION_CATALOGUE.values()), (
    "catalogue severities must be real incident severities"
)


def is_known_action(action_type: str) -> bool:
    """Deterministic catalogue membership check for an arbitrary string. Fails
    closed — anything not an exact :class:`RemediationActionType` value is
    unknown."""

    return action_type in _CATALOGUE_KEYS


_CATALOGUE_KEYS: frozenset[str] = frozenset(str(a) for a in RemediationActionType)


def get_action_definition(action_type: RemediationActionType) -> ActionDefinition | None:
    return _CATALOGUE.get(action_type)


def require_action_definition(action_type: RemediationActionType | str) -> ActionDefinition:
    """Return the definition or raise :class:`UnknownActionError` (fail closed)."""

    if isinstance(action_type, RemediationActionType):
        return _CATALOGUE[action_type]
    try:
        resolved = RemediationActionType(action_type)
    except ValueError as exc:
        raise UnknownActionError(f"unknown remediation action {action_type!r}") from exc
    return _CATALOGUE[resolved]


def is_allowed_target(action_type: RemediationActionType, target: ServiceTarget) -> bool:
    """True iff ``action_type`` is a catalogue action allowed against ``target``.

    Requires: the action is known, the target type is permitted, the service is
    on both the global allow-list and the action's own list. Fails closed.
    """

    definition = get_action_definition(action_type)
    if definition is None:
        return False
    return (
        target.target_type in definition.allowed_target_types
        and is_allowed_service(target.service_name)
        and target.service_name in definition.allowed_target_services
    )


def validate_action_parameters(
    action_type: RemediationActionType, parameters: Mapping[str, object]
) -> dict[str, ParameterValue]:
    """Validate ``parameters`` against the catalogue's bounded schema.

    Fails closed on: an unknown action, an unknown parameter key, a missing
    required parameter, a wrong value type, an out-of-range int, a string that
    fails the parameter's ``pattern`` or ``allowed_values``. Returns a plain,
    normalized ``dict`` on success.
    """

    definition = require_action_definition(action_type)
    specs = {p.name: p for p in definition.parameters}

    unknown = set(parameters) - set(specs)
    if unknown:
        raise ParameterValidationError(f"unknown parameter(s) for {action_type}: {sorted(unknown)}")

    missing = definition.required_parameter_names() - set(parameters)
    if missing:
        raise ParameterValidationError(
            f"missing required parameter(s) for {action_type}: {sorted(missing)}"
        )

    normalized: dict[str, ParameterValue] = {}
    for name, spec in specs.items():
        if name not in parameters:
            continue
        normalized[name] = _validate_one(action_type, spec, parameters[name])
    return normalized


def _validate_one(
    action_type: RemediationActionType, spec: ActionParameter, value: object
) -> ParameterValue:
    if spec.value_type == "int":
        # bool is a subclass of int — reject it explicitly.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ParameterValidationError(
                f"{action_type}.{spec.name} must be an int, got {type(value).__name__}"
            )
        if spec.min_value is not None and value < spec.min_value:
            raise ParameterValidationError(f"{action_type}.{spec.name} must be >= {spec.min_value}")
        if spec.max_value is not None and value > spec.max_value:
            raise ParameterValidationError(f"{action_type}.{spec.name} must be <= {spec.max_value}")
        return value

    if not isinstance(value, str):
        raise ParameterValidationError(
            f"{action_type}.{spec.name} must be a string, got {type(value).__name__}"
        )
    if spec.allowed_values is not None and value not in spec.allowed_values:
        raise ParameterValidationError(
            f"{action_type}.{spec.name} must be one of {list(spec.allowed_values)}"
        )
    if spec.pattern is not None and not re.fullmatch(spec.pattern, value):
        raise ParameterValidationError(
            f"{action_type}.{spec.name} does not match the allowed pattern"
        )
    return value
