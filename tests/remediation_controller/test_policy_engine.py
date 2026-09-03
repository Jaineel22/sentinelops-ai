"""PolicyEngine.evaluate + apply_policy_decision (Phase 5B)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest

from remediation_controller.domain import (
    RemediationProposal,
    RemediationStatus,
    ServiceTarget,
)
from remediation_controller.domain.state_machine import allowed_transitions
from remediation_controller.policy import (
    POLICY_VERSION,
    PolicyConfig,
    PolicyEngine,
    PolicyError,
    PolicyOutcome,
    PolicyReasonCode,
    apply_policy_decision,
)
from remediation_controller.policy.rules import RULES
from tests.remediation_controller.policy_fakes import BASE_TIME, make_context

_ProposalFactory = Callable[..., RemediationProposal]


def test_valid_proposal_passes(proposal_factory: _ProposalFactory) -> None:
    decision = PolicyEngine().evaluate(proposal_factory(), make_context())
    assert decision.outcome is PolicyOutcome.ALLOW
    assert decision.allowed and decision.approved_for_human_review
    assert decision.violations == ()
    assert PolicyReasonCode.APPROVAL_REQUIRED in decision.reason_codes
    assert PolicyReasonCode.POLICY_OK in decision.reason_codes


def test_decision_carries_policy_version_and_evaluated_rules(
    proposal_factory: _ProposalFactory,
) -> None:
    decision = PolicyEngine().evaluate(proposal_factory(), make_context())
    assert decision.policy_version == POLICY_VERSION == "1"
    assert decision.evaluated_rules == tuple(r.__name__ for r in RULES)
    assert decision.evaluated_at == BASE_TIME


def test_evaluation_is_deterministic(proposal_factory: _ProposalFactory) -> None:
    engine, proposal, ctx = PolicyEngine(), proposal_factory(), make_context()
    assert engine.evaluate(proposal, ctx) == engine.evaluate(proposal, ctx)


def test_multiple_failures_are_all_reported_deterministically(
    proposal_factory: _ProposalFactory, target_factory: Callable[..., ServiceTarget]
) -> None:
    # wrong env + disallowed severity + expired, all at once
    proposal = proposal_factory(
        target=target_factory(service_name="orders-service", environment="production"),
    )
    ctx = make_context(incident_severity="LOW", now=BASE_TIME + timedelta(hours=2))
    decision = PolicyEngine().evaluate(proposal, ctx)
    assert decision.outcome is PolicyOutcome.DENY
    assert decision.reason_codes == tuple(sorted(decision.reason_codes, key=lambda c: c.value))
    assert set(decision.reason_codes) == {
        PolicyReasonCode.ENVIRONMENT_NOT_ALLOWED,
        PolicyReasonCode.SEVERITY_NOT_ALLOWED,
        PolicyReasonCode.PROPOSAL_EXPIRED,
    }
    # a second run yields an identical decision
    assert PolicyEngine().evaluate(proposal, ctx) == decision


def test_apply_allow_advances_to_pending_approval_only(
    proposal_factory: _ProposalFactory,
) -> None:
    proposal = proposal_factory()
    decision = PolicyEngine().evaluate(proposal, make_context())
    advanced = apply_policy_decision(proposal, decision)
    assert advanced.status is RemediationStatus.PENDING_APPROVAL
    # a human approval is still required before anything can execute
    assert RemediationStatus.EXECUTING not in {advanced.status}
    assert advanced.requires_approval is True
    # from PENDING_APPROVAL the only way forward is a human decision
    assert allowed_transitions(advanced.status) == frozenset(
        {RemediationStatus.APPROVED, RemediationStatus.REJECTED, RemediationStatus.EXPIRED}
    )


def test_apply_deny_blocks(proposal_factory: _ProposalFactory) -> None:
    proposal = proposal_factory()
    ctx = make_context(incident_severity="LOW")  # RESTART_SERVICE not eligible at LOW
    decision = PolicyEngine().evaluate(proposal, ctx)
    assert decision.outcome is PolicyOutcome.DENY
    blocked = apply_policy_decision(proposal, decision)
    assert blocked.status is RemediationStatus.BLOCKED
    assert blocked.status.is_terminal


def test_apply_never_yields_executing_or_executed(proposal_factory: _ProposalFactory) -> None:
    proposal = proposal_factory()
    for ctx in (make_context(), make_context(incident_severity="LOW")):
        decision = PolicyEngine().evaluate(proposal, ctx)
        result = apply_policy_decision(proposal, decision)
        assert result.status not in {
            RemediationStatus.EXECUTING,
            RemediationStatus.EXECUTED,
            RemediationStatus.APPROVED,
            RemediationStatus.VERIFYING,
            RemediationStatus.RECOVERED,
        }


@pytest.mark.parametrize(
    "status",
    [
        RemediationStatus.APPROVED,
        RemediationStatus.EXECUTING,
        RemediationStatus.EXECUTED,
        RemediationStatus.REJECTED,
        RemediationStatus.BLOCKED,
        RemediationStatus.RECOVERED,
    ],
)
def test_apply_refuses_non_input_states(
    proposal_factory: _ProposalFactory, status: RemediationStatus
) -> None:
    proposal = proposal_factory().model_copy(update={"status": status})
    decision = PolicyEngine().evaluate(proposal, make_context())
    with pytest.raises(PolicyError):
        apply_policy_decision(proposal, decision)


def test_custom_config_can_disable_an_action(proposal_factory: _ProposalFactory) -> None:
    config = PolicyConfig(eligible_actions=frozenset())
    decision = PolicyEngine(config).evaluate(proposal_factory(), make_context())
    assert decision.outcome is PolicyOutcome.DENY
    assert PolicyReasonCode.ACTION_NOT_ALLOWED in decision.reason_codes
