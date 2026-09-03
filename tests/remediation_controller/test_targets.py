"""The structural target model + the closed service allow-list (spec section 3)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from remediation_controller.domain import (
    ALLOWED_TARGET_SERVICES,
    ServiceTarget,
    is_allowed_service,
    resolve_target,
)
from remediation_controller.domain.errors import UnknownTargetError


def test_allow_list_is_minimal_and_code_defined() -> None:
    assert frozenset({"orders-service"}) == ALLOWED_TARGET_SERVICES


def test_allow_listed_service_resolves() -> None:
    target = ServiceTarget(service_name="orders-service", environment="development")
    assert resolve_target(target) is target
    assert is_allowed_service("orders-service")


def test_unknown_service_fails_closed() -> None:
    target = ServiceTarget(service_name="payments-service", environment="development")
    assert not is_allowed_service("payments-service")
    with pytest.raises(UnknownTargetError):
        resolve_target(target)


@pytest.mark.parametrize(
    "bad_name",
    [
        "orders-service; rm -rf /",
        "orders service",
        "../etc/passwd",
        "http://evil/",
        "Orders-Service",
        "$(reboot)",
        "a",  # too short
    ],
)
def test_service_name_rejects_non_slugs(bad_name: str) -> None:
    with pytest.raises(ValidationError):
        ServiceTarget(service_name=bad_name, environment="development")


def test_unknown_environment_rejected() -> None:
    with pytest.raises(ValidationError):
        ServiceTarget(service_name="orders-service", environment="prod")


def test_target_is_frozen_and_forbids_extra_fields() -> None:
    target = ServiceTarget(service_name="orders-service", environment="development")
    with pytest.raises(ValidationError):
        target.service_name = "other"
    with pytest.raises(ValidationError):
        ServiceTarget.model_validate(
            {
                "service_name": "orders-service",
                "environment": "development",
                "command": "rm -rf /",
            }
        )
