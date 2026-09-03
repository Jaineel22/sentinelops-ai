"""The remediation proposal model (spec section 4).

Central guarantee: the proposal represents *intent* and has no field — and can
grow no field — that holds an executable command.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from remediation_controller.domain import (
    RemediationActionType,
    RemediationProposal,
    RemediationStatus,
    ServiceTarget,
)

_TargetFactory = Callable[..., ServiceTarget]
_ProposalFactory = Callable[..., RemediationProposal]


def _valid_payload() -> dict[str, Any]:
    return {
        "remediation_id": "rem_00112233aabbccdd",
        "incident_id": "inc_00112233aabbccdd",
        "trigger": "MANUAL",
        "proposed_by": "operator:alice",
        "action_type": "RESTART_SERVICE",
        "target": {"service_name": "orders-service", "environment": "development"},
        "risk_level": "MEDIUM",
        "created_at": "2026-09-02T12:00:00Z",
        "expires_at": "2026-09-02T13:00:00Z",
    }


def test_valid_proposal_is_accepted(proposal_factory: _ProposalFactory) -> None:
    proposal = proposal_factory()
    assert proposal.status is RemediationStatus.PROPOSED
    assert proposal.requires_approval is True
    assert proposal.action_type is RemediationActionType.RESTART_SERVICE


def test_valid_proposal_round_trips_from_json() -> None:
    proposal = RemediationProposal.model_validate(_valid_payload())
    assert proposal.action_type is RemediationActionType.RESTART_SERVICE
    assert str(proposal.target) == "orders-service:development"


@pytest.mark.parametrize(
    "bad_field",
    ["command", "script", "shell", "docker_command", "kubectl_command", "cmd", "run"],
)
def test_arbitrary_command_field_is_impossible(bad_field: str) -> None:
    payload = _valid_payload()
    payload[bad_field] = "rm -rf / --no-preserve-root"
    with pytest.raises(ValidationError):
        RemediationProposal.model_validate(payload)


def test_proposal_model_has_no_command_like_attribute() -> None:
    fields = set(RemediationProposal.model_fields)
    for banned in ("command", "script", "shell", "cmd", "exec", "run", "payload"):
        assert banned not in fields


def test_requires_approval_cannot_be_set_false() -> None:
    with pytest.raises(ValidationError):
        RemediationProposal.model_validate({**_valid_payload(), "requires_approval": False})


def test_proposal_is_frozen(proposal_factory: _ProposalFactory) -> None:
    proposal = proposal_factory()
    with pytest.raises(ValidationError):
        proposal.status = RemediationStatus.APPROVED


def test_invalid_action_rejected() -> None:
    with pytest.raises(ValidationError):
        RemediationProposal.model_validate({**_valid_payload(), "action_type": "DELETE_EVERYTHING"})


def test_invalid_target_rejected(
    proposal_factory: _ProposalFactory, target_factory: _TargetFactory
) -> None:
    with pytest.raises(ValidationError):
        proposal_factory(target=target_factory(service_name="payments-service"))


def test_parameters_validated_against_catalogue(proposal_factory: _ProposalFactory) -> None:
    ok = proposal_factory(
        action_type=RemediationActionType.SCALE_SERVICE, parameters={"replicas": 3}
    )
    assert ok.parameters == {"replicas": 3}

    with pytest.raises(ValidationError):
        proposal_factory(
            action_type=RemediationActionType.SCALE_SERVICE, parameters={"replicas": 999}
        )
    with pytest.raises(ValidationError):
        proposal_factory(
            action_type=RemediationActionType.RESTART_SERVICE,
            parameters={"surprise": "value"},
        )


def test_bad_id_patterns_rejected() -> None:
    with pytest.raises(ValidationError):
        RemediationProposal.model_validate({**_valid_payload(), "remediation_id": "nope"})
    with pytest.raises(ValidationError):
        RemediationProposal.model_validate({**_valid_payload(), "incident_id": "12345"})


def test_expiry_must_be_after_creation() -> None:
    payload = _valid_payload()
    payload["expires_at"] = payload["created_at"]
    with pytest.raises(ValidationError):
        RemediationProposal.model_validate(payload)
