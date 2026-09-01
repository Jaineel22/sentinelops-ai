"""Phase 4 domain vocabulary — enums only.

Kept free of Pydantic, SQLAlchemy, and framework imports so the investigation
state machine and the validation rules stay pure and trivially unit-testable
(same discipline as ``incident_correlator.domain``).
"""

from __future__ import annotations

from enum import StrEnum


class InvestigationStatus(StrEnum):
    """Lifecycle of a single RCA investigation.

    The blueprint flow — receive incident -> plan -> bounded tool loop ->
    correlate evidence -> determine root cause -> recommend action -> structured
    output — maps onto these phases. Transitions are enforced by
    :mod:`rca_agent.state_machine`.
    """

    PENDING = "PENDING"
    PLANNING = "PLANNING"
    COLLECTING_EVIDENCE = "COLLECTING_EVIDENCE"
    ANALYZING = "ANALYZING"
    VERIFYING = "VERIFYING"
    # --- terminal ---
    COMPLETED = "COMPLETED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL


_TERMINAL: frozenset[InvestigationStatus] = frozenset(
    {
        InvestigationStatus.COMPLETED,
        InvestigationStatus.INSUFFICIENT_EVIDENCE,
        InvestigationStatus.FAILED,
        InvestigationStatus.TIMED_OUT,
    }
)

TERMINAL_STATUSES: frozenset[InvestigationStatus] = _TERMINAL
ACTIVE_STATUSES: frozenset[InvestigationStatus] = frozenset(
    s for s in InvestigationStatus if s not in _TERMINAL
)


class InvestigationTrigger(StrEnum):
    EVENT = "EVENT"  # consumed ``incident.opened`` from Kafka
    MANUAL = "MANUAL"  # POST /investigations


class Confidence(StrEnum):
    UNKNOWN = "UNKNOWN"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

    @property
    def rank(self) -> int:
        """Ordered comparison. ``StrEnum`` compares by string value, so callers
        must use ``.rank`` (mirrors ``incident_correlator.domain.Severity``)."""

        return _CONFIDENCE_ORDER.index(self)


_CONFIDENCE_ORDER: tuple[Confidence, ...] = (
    Confidence.UNKNOWN,
    Confidence.LOW,
    Confidence.MEDIUM,
    Confidence.HIGH,
)


class EvidenceSourceType(StrEnum):
    """The conceptual evidence taxonomy from the architecture blueprint.

    Not all of these have a backing data source in the current repository. The
    *tool registry* (Sub-phase 4B) registers only the ones that can actually be
    served; the rest are surfaced to the agent as explicitly unavailable, never
    fabricated. Availability is tracked there, not here — this enum is only the
    vocabulary.
    """

    INCIDENT = "incident"  # Phase 3 Incident API
    ANOMALY = "anomaly"  # Phase 3 incident evidence (Phase 2 model output)
    METRIC = "metric"  # Prometheus /metrics scrape of an instrumented service
    SERVICE_HEALTH = "service_health"  # /health + /ready of an instrumented service
    RELATED_INCIDENT = "related_incident"  # other incidents for the same service
    LOG = "log"  # NOT AVAILABLE — no log aggregation backend yet (Phase 7)
    TRACE = "trace"  # NOT AVAILABLE — no trace backend yet (Phase 7)
    DEPLOYMENT = "deployment"  # NOT AVAILABLE — no deployment metadata source yet
    SERVICE_DEPENDENCY = "service_dependency"  # NOT AVAILABLE — no dependency graph yet


class TrustLevel(StrEnum):
    """Provenance of an evidence item.

    ``TRUSTED_SYSTEM`` — produced by SentinelOps' own control plane (the Incident
    API, our own metrics endpoints). ``UNTRUSTED_EXTERNAL`` — free-form text that
    originated outside our control plane (log lines, trace tags, deployment
    notes) and could carry a prompt-injection payload.

    The prompt-injection defense (Sub-phase 4C) is **unconditional** — every tool
    output is treated as data, never instructions, regardless of trust level.
    This field only records where the data came from for the human reader.
    """

    TRUSTED_SYSTEM = "TRUSTED_SYSTEM"
    UNTRUSTED_EXTERNAL = "UNTRUSTED_EXTERNAL"


class FindingType(StrEnum):
    OBSERVATION = "observation"
    CORRELATION = "correlation"
    HYPOTHESIS = "hypothesis"
    ROOT_CAUSE = "root_cause"
    CONTRIBUTING_FACTOR = "contributing_factor"


class HypothesisVerdict(StrEnum):
    SUPPORTED = "SUPPORTED"
    REFUTED = "REFUTED"
    UNVERIFIED = "UNVERIFIED"
    CONFLICTING = "CONFLICTING"


class RecommendedActionType(StrEnum):
    """Closed set of remediation *categories* the agent may recommend.

    These are labels on a recommendation for a human, NOT executable commands.
    Phase 4 has no executor: nothing in this service can act on any of these.
    Phase 5 will map an approved recommendation onto an allow-listed action
    (ADR-003). Keeping this a closed enum means a prompt injection cannot make
    the agent "recommend" an arbitrary command — there is no field for one.
    """

    INVESTIGATE_FURTHER = "INVESTIGATE_FURTHER"
    MONITOR = "MONITOR"
    RESTART_SERVICE = "RESTART_SERVICE"
    ROLL_BACK_DEPLOYMENT = "ROLL_BACK_DEPLOYMENT"
    SCALE_SERVICE = "SCALE_SERVICE"
    ADJUST_CONFIGURATION = "ADJUST_CONFIGURATION"
    FAILOVER_DEPENDENCY = "FAILOVER_DEPENDENCY"
    CONTACT_SERVICE_OWNER = "CONTACT_SERVICE_OWNER"
    NO_ACTION_NEEDED = "NO_ACTION_NEEDED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


class StepKind(StrEnum):
    """Categories for the operational investigation trace.

    The trace stores *what the agent did and why* in concise operational terms
    (e.g. "queried orders-service latency metrics because the incident affected
    checkout latency"). It never stores private model chain-of-thought.
    """

    PLAN = "PLAN"
    TOOL_CALL = "TOOL_CALL"
    TOOL_RESULT = "TOOL_RESULT"
    ANALYSIS = "ANALYSIS"
    HYPOTHESIS = "HYPOTHESIS"
    VERIFICATION = "VERIFICATION"
    RCA = "RCA"
    VALIDATION = "VALIDATION"
    LIMIT = "LIMIT"
    ERROR = "ERROR"
