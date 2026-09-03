"""Deterministic inputs to policy evaluation.

* :class:`PolicyConfig` — the static policy knobs (versioned, code-defined).
* :class:`PolicyContext` — the per-evaluation runtime facts the policy engine is
  *given* rather than trusting the proposal for (the current wall clock, the
  incident severity verified independently of the RCA agent, and a port onto
  prior remediation history).
* :class:`RemediationHistoryPort` — the injectable abstraction 5C will back with
  PostgreSQL. Phase 5B ships only :class:`NullRemediationHistory` (a null object,
  not a fake persistence implementation — it reports "nothing known").

Nothing here imports an LLM, a database driver, a Kafka client, or an executor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from remediation_controller.domain.enums import RemediationActionType, RiskLevel
from remediation_controller.domain.models import ServiceTarget
from remediation_controller.policy.codes import POLICY_VERSION

# Incident severities, re-declared (mirrors ``incident_correlator.domain.Severity``)
# to keep the service boundary clean — the same choice the 5A catalogue made.
KNOWN_SEVERITIES: frozenset[str] = frozenset({"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"})


class PolicyConfig(BaseModel):
    """Static, code-defined policy configuration. Immutable; not sourced from
    the environment or any untrusted input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_version: str = POLICY_VERSION

    # An action must be BOTH in the closed catalogue AND enabled here to pass.
    # Defaults to every catalogue action; narrow it to disable one without
    # touching the catalogue.
    eligible_actions: frozenset[RemediationActionType] = frozenset(RemediationActionType)

    # Phase 5B deliberately only permits the one environment that has a real
    # (instrumented) service. Staging / production stay closed until a real
    # executor and their own review exist.
    allowed_environments: frozenset[str] = frozenset({"development"})

    # Deterministic ceilings. Risk and blast radius are read from the catalogue
    # ``ActionDefinition`` — never from ``proposal.risk_level`` (which an upstream
    # LLM-derived mapping may have set).
    max_allowed_risk: RiskLevel = RiskLevel.HIGH
    max_blast_radius: int = Field(default=10, ge=1, le=50)

    # Fail closed when the incident severity cannot be independently verified.
    require_known_incident_severity: bool = True


@dataclass(frozen=True)
class RemediationHistoryEntry:
    """A prior remediation for the same incident + action + target."""

    remediation_id: str
    completed_at: datetime | None  # None while still in flight


class RemediationHistoryPort(Protocol):
    """Read-only view of prior remediations, keyed by incident + action + target.

    5C backs this with PostgreSQL. The policy engine only ever *reads* through it
    and treats every method as best-effort — an unavailable history source must
    not crash policy evaluation (the adapter returns the safe default).
    """

    def active_remediation_exists(
        self,
        *,
        incident_id: str,
        action_type: RemediationActionType,
        target: ServiceTarget,
    ) -> bool: ...

    def last_completed_at(
        self,
        *,
        incident_id: str,
        action_type: RemediationActionType,
        target: ServiceTarget,
    ) -> datetime | None: ...


class NullRemediationHistory:
    """The default history port: reports that nothing is known.

    Not a fake persistence layer — it is the honest "no history source wired
    yet" null object. With it, the cooldown / duplicate rule can never fire, so
    5B stays conservative-but-usable until 5C provides a real adapter.
    """

    def active_remediation_exists(
        self,
        *,
        incident_id: str,
        action_type: RemediationActionType,
        target: ServiceTarget,
    ) -> bool:
        return False

    def last_completed_at(
        self,
        *,
        incident_id: str,
        action_type: RemediationActionType,
        target: ServiceTarget,
    ) -> datetime | None:
        return None


@dataclass(frozen=True)
class PolicyContext:
    """Per-evaluation facts supplied by the caller (never inferred from RCA text).

    ``now`` makes evaluation deterministic and testable. ``incident_severity`` is
    the severity the caller verified against the Incident API — ``None`` means
    "could not verify", which fails closed when
    ``PolicyConfig.require_known_incident_severity`` is set.
    """

    now: datetime
    incident_severity: str | None = None
    history: RemediationHistoryPort = field(default_factory=NullRemediationHistory)
