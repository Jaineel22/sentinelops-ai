"""The closed action catalogue (spec sections 1, 2) and target allow-list."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from remediation_controller.domain import (
    ACTION_CATALOGUE,
    RemediationActionType,
    RiskLevel,
    ServiceTarget,
    get_action_definition,
    is_allowed_target,
    is_known_action,
    require_action_definition,
    validate_action_parameters,
)
from remediation_controller.domain.errors import (
    ParameterValidationError,
    UnknownActionError,
)


def test_catalogue_is_total_and_closed() -> None:
    assert set(ACTION_CATALOGUE) == set(RemediationActionType)


def test_every_executable_action_requires_approval() -> None:
    for definition in ACTION_CATALOGUE.values():
        assert definition.requires_approval is True


def test_requires_approval_cannot_be_disabled() -> None:
    from remediation_controller.domain.catalogue import ActionDefinition

    definition = ACTION_CATALOGUE[RemediationActionType.RESTART_SERVICE]
    with pytest.raises(ValidationError):
        ActionDefinition(**{**definition.model_dump(), "requires_approval": False})


def test_catalogue_mapping_is_read_only() -> None:
    with pytest.raises(TypeError):
        ACTION_CATALOGUE["x"] = None  # type: ignore[index]


def test_catalogue_entries_are_frozen() -> None:
    definition = ACTION_CATALOGUE[RemediationActionType.RESTART_SERVICE]
    with pytest.raises(ValidationError):
        definition.risk_level = RiskLevel.LOW


@pytest.mark.parametrize("action", list(RemediationActionType))
def test_known_actions_resolve(action: RemediationActionType) -> None:
    assert is_known_action(str(action))
    assert get_action_definition(action) is not None
    assert require_action_definition(action).action_type is action
    assert require_action_definition(str(action)).action_type is action


@pytest.mark.parametrize(
    "unknown",
    [
        "execute_command",
        "run_shell",
        "kubectl_exec",
        "docker rm -f everything",
        "RESTART_SERVICE; rm -rf /",
        "",
        "restart_service",  # wrong case — not an exact enum value
        "TOTALLY_MADE_UP",
    ],
)
def test_unknown_actions_fail_closed(unknown: str) -> None:
    assert not is_known_action(unknown)
    with pytest.raises(UnknownActionError):
        require_action_definition(unknown)


def test_known_target_accepted() -> None:
    target = ServiceTarget(service_name="orders-service", environment="development")
    assert is_allowed_target(RemediationActionType.RESTART_SERVICE, target)


def test_unknown_target_rejected() -> None:
    target = ServiceTarget(service_name="payments-service", environment="development")
    assert not is_allowed_target(RemediationActionType.RESTART_SERVICE, target)


def test_scale_service_parameter_schema() -> None:
    assert validate_action_parameters(RemediationActionType.SCALE_SERVICE, {"replicas": 3}) == {
        "replicas": 3
    }

    with pytest.raises(ParameterValidationError):  # missing required
        validate_action_parameters(RemediationActionType.SCALE_SERVICE, {})
    with pytest.raises(ParameterValidationError):  # out of range
        validate_action_parameters(RemediationActionType.SCALE_SERVICE, {"replicas": 99})
    with pytest.raises(ParameterValidationError):  # unknown key
        validate_action_parameters(
            RemediationActionType.SCALE_SERVICE, {"replicas": 3, "namespace": "prod"}
        )
    with pytest.raises(ParameterValidationError):  # bool is not an int
        validate_action_parameters(RemediationActionType.SCALE_SERVICE, {"replicas": True})
    with pytest.raises(ParameterValidationError):  # wrong type
        validate_action_parameters(RemediationActionType.SCALE_SERVICE, {"replicas": "3"})


def test_string_parameter_is_pattern_bounded() -> None:
    assert validate_action_parameters(
        RemediationActionType.DISABLE_FEATURE_FLAG, {"flag_key": "new_checkout_flow"}
    ) == {"flag_key": "new_checkout_flow"}

    with pytest.raises(ParameterValidationError):
        validate_action_parameters(
            RemediationActionType.DISABLE_FEATURE_FLAG,
            {"flag_key": "flag; rm -rf /"},
        )
    with pytest.raises(ParameterValidationError):
        validate_action_parameters(
            RemediationActionType.ROLL_BACK_DEPLOYMENT,
            {"to_revision": "v1$(reboot)"},
        )


def test_no_catalogue_field_can_hold_a_command() -> None:
    # No parameter is unbounded free text: every string parameter has a pattern
    # or an allowed_values list.
    for definition in ACTION_CATALOGUE.values():
        for param in definition.parameters:
            if param.value_type == "string":
                assert param.pattern is not None or param.allowed_values is not None
