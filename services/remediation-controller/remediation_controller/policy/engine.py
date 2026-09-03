"""The deterministic policy engine.

``PolicyEngine.evaluate(proposal, context) -> PolicyDecision`` runs every rule in
:data:`~remediation_controller.policy.rules.RULES`, collects every violation, and
returns a structured, reproducible decision. No LLM, no I/O, no mutation.

``apply_policy_decision(proposal, decision) -> RemediationProposal`` is the only
place a decision advances a proposal's lifecycle. It uses the **Phase 5A** state
machine and can only ever land on ``PENDING_APPROVAL`` (policy passed — a human
must still approve) or ``BLOCKED`` (policy denied). It can never produce
``EXECUTING`` or ``EXECUTED``.
"""

from __future__ import annotations

from remediation_controller.domain.enums import RemediationStatus
from remediation_controller.domain.proposal import RemediationProposal
from remediation_controller.domain.state_machine import validate_transition
from remediation_controller.policy.codes import POLICY_VERSION, PolicyOutcome, PolicyReasonCode
from remediation_controller.policy.context import PolicyConfig, PolicyContext
from remediation_controller.policy.decision import PolicyDecision, PolicyViolation
from remediation_controller.policy.errors import PolicyError
from remediation_controller.policy.rules import POLICY_INPUT_STATES, RULES

# Statuses a policy decision must never, ever produce (defensive assertion).
_FORBIDDEN_RESULT_STATES: frozenset[RemediationStatus] = frozenset(
    {
        RemediationStatus.APPROVED,
        RemediationStatus.EXECUTING,
        RemediationStatus.EXECUTED,
        RemediationStatus.VERIFYING,
        RemediationStatus.RECOVERED,
    }
)

# Deterministic lifecycle paths through the Phase 5A state machine.
_PATHS: dict[tuple[RemediationStatus, RemediationStatus], tuple[RemediationStatus, ...]] = {
    (RemediationStatus.PROPOSED, RemediationStatus.PENDING_APPROVAL): (
        RemediationStatus.POLICY_EVALUATION,
        RemediationStatus.PENDING_APPROVAL,
    ),
    (RemediationStatus.PROPOSED, RemediationStatus.BLOCKED): (RemediationStatus.BLOCKED,),
    (RemediationStatus.POLICY_EVALUATION, RemediationStatus.PENDING_APPROVAL): (
        RemediationStatus.PENDING_APPROVAL,
    ),
    (RemediationStatus.POLICY_EVALUATION, RemediationStatus.BLOCKED): (RemediationStatus.BLOCKED,),
}


class PolicyEngine:
    """Deterministic. Construct once with a :class:`PolicyConfig`; reuse freely."""

    def __init__(self, config: PolicyConfig | None = None) -> None:
        self._config = config or PolicyConfig()

    @property
    def config(self) -> PolicyConfig:
        return self._config

    def evaluate(self, proposal: RemediationProposal, context: PolicyContext) -> PolicyDecision:
        violations: list[PolicyViolation] = []
        evaluated: list[str] = []
        for rule in RULES:
            evaluated.append(rule.__name__)
            violations.extend(rule(proposal, context, self._config))

        if violations:
            codes = tuple(sorted({v.code for v in violations}, key=lambda c: c.value))
            return PolicyDecision(
                outcome=PolicyOutcome.DENY,
                reason_codes=codes,
                violations=tuple(violations),
                policy_version=self._config.policy_version,
                evaluated_rules=tuple(evaluated),
                evaluated_at=context.now,
            )

        return PolicyDecision(
            outcome=PolicyOutcome.ALLOW,
            # APPROVAL_REQUIRED is always present: policy passing is not an
            # execution authority — a human approval is still mandatory.
            reason_codes=(PolicyReasonCode.POLICY_OK, PolicyReasonCode.APPROVAL_REQUIRED),
            violations=(),
            policy_version=self._config.policy_version,
            evaluated_rules=tuple(evaluated),
            evaluated_at=context.now,
        )


def apply_policy_decision(
    proposal: RemediationProposal, decision: PolicyDecision
) -> RemediationProposal:
    """Advance ``proposal`` per ``decision`` using the Phase 5A state machine.

    ``ALLOW`` -> ``PENDING_APPROVAL`` (via ``POLICY_EVALUATION``).
    ``DENY``  -> ``BLOCKED``.

    Raises :class:`PolicyError` if the proposal is not in a policy-input state
    (``PROPOSED`` / ``POLICY_EVALUATION``). Never returns ``EXECUTING`` /
    ``EXECUTED`` / ``APPROVED`` — asserted before returning.
    """

    if proposal.status not in POLICY_INPUT_STATES:
        raise PolicyError(
            f"cannot apply a policy decision to a proposal in status {proposal.status}"
        )

    target = RemediationStatus.PENDING_APPROVAL if decision.allowed else RemediationStatus.BLOCKED
    path = _PATHS[(proposal.status, target)]

    result = proposal
    for nxt in path:
        validate_transition(result.status, nxt)  # Phase 5A guard
        result = result.model_copy(update={"status": nxt})

    if result.status in _FORBIDDEN_RESULT_STATES:  # pragma: no cover - defensive
        raise PolicyError(f"policy decision produced a forbidden status {result.status}")
    return result


__all__ = ["POLICY_VERSION", "PolicyEngine", "apply_policy_decision"]
