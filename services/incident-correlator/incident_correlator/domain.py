"""Incident domain model — enums and value objects.

Kept free of persistence and framework concerns so the correlation / severity /
state-machine logic is pure and unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class IncidentStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    INVESTIGATING = "INVESTIGATING"
    MITIGATING = "MITIGATING"
    RESOLVED = "RESOLVED"

    @property
    def is_active(self) -> bool:
        return self is not IncidentStatus.RESOLVED


ACTIVE_STATUSES: frozenset[IncidentStatus] = frozenset(s for s in IncidentStatus if s.is_active)


class IncidentRelationType(StrEnum):
    """How two incidents are linked (Phase 8 — cross-service correlation)."""

    DEPENDENCY = "dependency"  # a declared edge in the service-dependency graph
    CROSS_SERVICE = "cross_service"  # generic concurrent cross-service link


class Severity(StrEnum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return _SEVERITY_ORDER.index(self)


_SEVERITY_ORDER: tuple[Severity, ...] = (
    Severity.INFO,
    Severity.LOW,
    Severity.MEDIUM,
    Severity.HIGH,
    Severity.CRITICAL,
)


def correlation_key(service: str, environment: str) -> str:
    """The deterministic grouping key (ADR-015). One active incident per key."""

    return f"{service}:{environment}"


@dataclass(frozen=True)
class AnomalySignal:
    """A single anomaly observation, normalised from an ``anomaly.detected`` event."""

    event_id: str
    detector: str
    detector_version: str
    service: str
    environment: str
    window_start: datetime
    window_end: datetime
    anomaly_score: float
    threshold: float
    signals: dict[str, float]
    abnormal_signals: list[str]
    trace_id: str | None
    occurred_at: datetime

    @property
    def correlation_key(self) -> str:
        return correlation_key(self.service, self.environment)


@dataclass
class EvidenceRecord:
    event_id: str
    detector: str
    detector_version: str
    anomaly_score: float
    threshold: float
    window_start: datetime
    window_end: datetime
    signals: dict[str, float]
    abnormal_signals: list[str]
    trace_id: str | None
    occurred_at: datetime
    correlation_reason: str


@dataclass(frozen=True)
class IncidentRelation:
    """A directed link between two incidents (dependent -> dependency)."""

    incident_id: str
    related_incident_id: str
    relation_type: IncidentRelationType
    reason: str
    created_at: datetime


@dataclass
class StateTransition:
    from_status: IncidentStatus | None
    to_status: IncidentStatus
    actor: str
    reason: str
    severity_at_transition: Severity | None
    created_at: datetime


@dataclass
class Incident:
    id: str
    correlation_key: str
    service: str
    environment: str
    status: IncidentStatus
    severity: Severity
    severity_reasons: list[str]
    title: str
    anomaly_count: int
    max_anomaly_score: float
    max_error_rate: float
    max_latency_p95_ms: float
    detector: str
    started_at: datetime
    last_evidence_at: datetime
    created_at: datetime
    updated_at: datetime
    # Names of every signal that has been abnormal at least once. Kept as a small
    # bounded list so ``distinct_abnormal_signals`` is O(1) to maintain on append
    # (no scan of the evidence history).
    abnormal_signal_names: list[str] = field(default_factory=list)
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    resolution: str | None = None
    evidence: list[EvidenceRecord] = field(default_factory=list)
    history: list[StateTransition] = field(default_factory=list)

    @property
    def distinct_abnormal_signals(self) -> int:
        return len(self.abnormal_signal_names)

    @property
    def duration_seconds(self) -> float:
        return max((self.last_evidence_at - self.started_at).total_seconds(), 0.0)
