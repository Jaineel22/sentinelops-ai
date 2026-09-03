"""Every Phase 5B policy rule, pass and fail. Fail closed."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest

from remediation_controller.domain import (
    RemediationActionType,
    RemediationProposal,
    RemediationStatus,
    RiskLevel,
    ServiceTarget,
)
from remediation_controller.policy import PolicyConfig, PolicyReasonCode, PolicyViolation
from remediation_controller.policy.rules import (
    action_rule,
    cooldown_rule,
    environment_rule,
    expiry_rule,
    parameter_rule,
    risk_rule,
    severity_rule,
    state_rule,
    target_rule,
)
from tests.remediation_controller.policy_fakes import BASE_TIME, FakeHistory, make_context

_ProposalFactory = Callable[..., RemediationProposal]
_TargetFactory = Callable[..., ServiceTarget]
_CONFIG = PolicyConfig()


def _codes(violations: list[PolicyViolation]) -> set[PolicyReasonCode]:
    return {v.code for v in violations}


# --- state ------------------------------------------------------------
def test_state_rule_passes_for_proposed(proposal_factory: _ProposalFactory) -> None:
    assert state_rule(proposal_factory(), make_context(), _CONFIG) == []


@pytest.mark.parametrize(
    "status",
    [
        RemediationStatus.PENDING_APPROVAL,
        RemediationStatus.APPROVED,
        RemediationStatus.EXECUTING,
        RemediationStatus.EXECUTED,
        RemediationStatus.VERIFYING,
        RemediationStatus.BLOCKED,
        RemediationStatus.REJECTED,
        RemediationStatus.EXPIRED,
        RemediationStatus.EXECUTION_FAILED,
        RemediationStatus.RECOVERED,
        RemediationStatus.RECOVERY_FAILED,
    ],
)
def test_state_rule_blocks_every_non_input_status(
    proposal_factory: _ProposalFactory, status: RemediationStatus
) -> None:
    proposal = proposal_factory().model_copy(update={"status": status})
    assert _codes(state_rule(proposal, make_context(), _CONFIG)) == {PolicyReasonCode.INVALID_STATE}


def test_policy_evaluation_status_is_also_accepted(proposal_factory: _ProposalFactory) -> None:
    proposal = proposal_factory().model_copy(update={"status": RemediationStatus.POLICY_EVALUATION})
    assert state_rule(proposal, make_context(), _CONFIG) == []


# --- action ----------------------------------------------------------
def test_action_rule_passes_for_catalogue_action(proposal_factory: _ProposalFactory) -> None:
    assert action_rule(proposal_factory(), make_context(), _CONFIG) == []


def test_action_rule_blocks_unknown_action(proposal_factory: _ProposalFactory) -> None:
    corrupt = proposal_factory().model_copy(update={"action_type": "DELETE_EVERYTHING"})
    assert _codes(action_rule(corrupt, make_context(), _CONFIG)) == {
        PolicyReasonCode.ACTION_NOT_ALLOWED
    }


def test_action_rule_blocks_action_disabled_by_policy(proposal_factory: _ProposalFactory) -> None:
    config = PolicyConfig(eligible_actions=frozenset({RemediationActionType.SCALE_SERVICE}))
    assert _codes(action_rule(proposal_factory(), make_context(), config)) == {
        PolicyReasonCode.ACTION_NOT_ALLOWED
    }


# --- target ----------------------------------------------------------
def test_target_rule_passes_for_allow_listed(proposal_factory: _ProposalFactory) -> None:
    assert target_rule(proposal_factory(), make_context(), _CONFIG) == []


def test_target_rule_blocks_unknown_service(
    proposal_factory: _ProposalFactory, target_factory: _TargetFactory
) -> None:
    proposal = proposal_factory().model_copy(
        update={"target": target_factory(service_name="payments-service")}
    )
    assert _codes(target_rule(proposal, make_context(), _CONFIG)) == {
        PolicyReasonCode.TARGET_NOT_ALLOWED
    }


# --- environment ---------------------------------------------------
def test_environment_rule_passes_for_development(proposal_factory: _ProposalFactory) -> None:
    assert environment_rule(proposal_factory(), make_context(), _CONFIG) == []


@pytest.mark.parametrize("env", ["staging", "production"])
def test_environment_rule_blocks_other_environments(
    proposal_factory: _ProposalFactory, target_factory: _TargetFactory, env: str
) -> None:
    proposal = proposal_factory().model_copy(update={"target": target_factory(environment=env)})
    assert _codes(environment_rule(proposal, make_context(), _CONFIG)) == {
        PolicyReasonCode.ENVIRONMENT_NOT_ALLOWED
    }


# --- severity ----------------------------------------------------
@pytest.mark.parametrize("severity", ["MEDIUM", "HIGH", "CRITICAL"])
def test_severity_rule_passes_for_eligible(
    proposal_factory: _ProposalFactory, severity: str
) -> None:
    assert (
        severity_rule(proposal_factory(), make_context(incident_severity=severity), _CONFIG) == []
    )


def test_severity_rule_blocks_disallowed_severity(proposal_factory: _ProposalFactory) -> None:
    assert _codes(
        severity_rule(proposal_factory(), make_context(incident_severity="LOW"), _CONFIG)
    ) == {PolicyReasonCode.SEVERITY_NOT_ALLOWED}


def test_severity_rule_fails_closed_when_unknown(proposal_factory: _ProposalFactory) -> None:
    assert _codes(
        severity_rule(proposal_factory(), make_context(incident_severity=None), _CONFIG)
    ) == {PolicyReasonCode.SEVERITY_NOT_ALLOWED}
    assert _codes(
        severity_rule(proposal_factory(), make_context(incident_severity="BOGUS"), _CONFIG)
    ) == {PolicyReasonCode.SEVERITY_NOT_ALLOWED}


def test_severity_rule_can_be_made_lenient(proposal_factory: _ProposalFactory) -> None:
    config = PolicyConfig(require_known_incident_severity=False)
    assert severity_rule(proposal_factory(), make_context(incident_severity=None), config) == []


# --- parameters -------------------------------------------------
def test_parameter_rule_passes_for_valid_params(proposal_factory: _ProposalFactory) -> None:
    proposal = proposal_factory(
        action_type=RemediationActionType.SCALE_SERVICE, parameters={"replicas": 3}
    )
    assert parameter_rule(proposal, make_context(), _CONFIG) == []


def test_parameter_rule_blocks_missing_required(proposal_factory: _ProposalFactory) -> None:
    proposal = proposal_factory(
        action_type=RemediationActionType.SCALE_SERVICE, parameters={"replicas": 3}
    ).model_copy(update={"parameters": {}})
    assert _codes(parameter_rule(proposal, make_context(), _CONFIG)) == {
        PolicyReasonCode.PARAMETER_INVALID
    }


def test_parameter_rule_blocks_out_of_range(proposal_factory: _ProposalFactory) -> None:
    proposal = proposal_factory(
        action_type=RemediationActionType.SCALE_SERVICE, parameters={"replicas": 3}
    ).model_copy(update={"parameters": {"replicas": 999}})
    assert _codes(parameter_rule(proposal, make_context(), _CONFIG)) == {
        PolicyReasonCode.PARAMETER_INVALID
    }


def test_parameter_rule_blocks_unknown_key(proposal_factory: _ProposalFactory) -> None:
    proposal = proposal_factory().model_copy(update={"parameters": {"command": "rm -rf /"}})
    assert _codes(parameter_rule(proposal, make_context(), _CONFIG)) == {
        PolicyReasonCode.PARAMETER_INVALID
    }


# --- risk / blast radius --------------------------------------
def test_risk_rule_passes_within_limits(proposal_factory: _ProposalFactory) -> None:
    assert risk_rule(proposal_factory(), make_context(), _CONFIG) == []


def test_risk_rule_blocks_when_catalogue_risk_exceeds_ceiling(
    proposal_factory: _ProposalFactory,
) -> None:
    # ROLL_BACK_DEPLOYMENT is HIGH risk in the catalogue.
    proposal = proposal_factory(action_type=RemediationActionType.ROLL_BACK_DEPLOYMENT)
    config = PolicyConfig(max_allowed_risk=RiskLevel.MEDIUM)
    assert _codes(risk_rule(proposal, make_context(), config)) == {PolicyReasonCode.RISK_EXCEEDED}


def test_risk_rule_uses_catalogue_risk_not_proposal_risk_level(
    proposal_factory: _ProposalFactory,
) -> None:
    # An upstream mapping under-declared the risk as LOW; policy ignores that.
    proposal = proposal_factory(
        action_type=RemediationActionType.ROLL_BACK_DEPLOYMENT, risk_level=RiskLevel.LOW
    )
    config = PolicyConfig(max_allowed_risk=RiskLevel.MEDIUM)
    assert _codes(risk_rule(proposal, make_context(), config)) == {PolicyReasonCode.RISK_EXCEEDED}


def test_risk_rule_blocks_blast_radius_from_replicas(proposal_factory: _ProposalFactory) -> None:
    proposal = proposal_factory(
        action_type=RemediationActionType.SCALE_SERVICE, parameters={"replicas": 9}
    )
    config = PolicyConfig(max_blast_radius=5)
    assert _codes(risk_rule(proposal, make_context(), config)) == {PolicyReasonCode.RISK_EXCEEDED}


# --- expiry ----------------------------------------------------
def test_expiry_rule_passes_before_expiry(proposal_factory: _ProposalFactory) -> None:
    assert expiry_rule(proposal_factory(), make_context(now=BASE_TIME), _CONFIG) == []


def test_expiry_rule_blocks_expired(proposal_factory: _ProposalFactory) -> None:
    ctx = make_context(now=BASE_TIME + timedelta(hours=2))
    assert _codes(expiry_rule(proposal_factory(), ctx, _CONFIG)) == {
        PolicyReasonCode.PROPOSAL_EXPIRED
    }


def test_expiry_rule_blocks_malformed_window(proposal_factory: _ProposalFactory) -> None:
    proposal = proposal_factory().model_copy(update={"expires_at": BASE_TIME - timedelta(hours=1)})
    assert _codes(
        expiry_rule(proposal, make_context(now=BASE_TIME - timedelta(hours=2)), _CONFIG)
    ) == {PolicyReasonCode.PROPOSAL_EXPIRED}


# --- cooldown / duplicate -------------------------------------
def test_cooldown_rule_passes_with_no_history(proposal_factory: _ProposalFactory) -> None:
    assert cooldown_rule(proposal_factory(), make_context(), _CONFIG) == []


def test_cooldown_rule_blocks_active_duplicate(proposal_factory: _ProposalFactory) -> None:
    ctx = make_context(history=FakeHistory(active=True))
    assert _codes(cooldown_rule(proposal_factory(), ctx, _CONFIG)) == {
        PolicyReasonCode.DUPLICATE_ACTIVE
    }


def test_cooldown_rule_blocks_within_cooldown_window(proposal_factory: _ProposalFactory) -> None:
    # RESTART_SERVICE cooldown is 300s; last completed 60s ago.
    ctx = make_context(history=FakeHistory(last_completed_at=BASE_TIME - timedelta(seconds=60)))
    assert _codes(cooldown_rule(proposal_factory(), ctx, _CONFIG)) == {
        PolicyReasonCode.COOLDOWN_ACTIVE
    }


def test_cooldown_rule_passes_after_cooldown(proposal_factory: _ProposalFactory) -> None:
    ctx = make_context(history=FakeHistory(last_completed_at=BASE_TIME - timedelta(seconds=600)))
    assert cooldown_rule(proposal_factory(), ctx, _CONFIG) == []
