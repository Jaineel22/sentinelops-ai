"""``AnomalyProcessor`` — the heart of Phase 3.

For each ``anomaly.detected`` event, in **one** database transaction:

1. **dedupe** — if evidence with this ``event_id`` already exists, do nothing
   (idempotent replay).
2. **lock** the single active incident for the anomaly's ``correlation_key``.
3. **decide** (pure): CREATE / APPEND / SUPERSEDE (:mod:`correlation`).
4. **apply** — create or update the incident, add the evidence row, recompute
   severity (:mod:`severity`), write a state-history row.
5. commit.

Only *after* the commit is a best-effort ``incident.*`` lifecycle event
published. The database is the source of truth; the lifecycle stream is a
wake-up for Phase 4.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from incident_correlator.correlation import (
    CorrelationAction,
    CorrelationConfig,
    CorrelationDecision,
    decide,
)
from incident_correlator.domain import (
    AnomalySignal,
    EvidenceRecord,
    Incident,
    IncidentRelation,
    IncidentRelationType,
    IncidentStatus,
    Severity,
    StateTransition,
)
from incident_correlator.events import lifecycle_envelope
from incident_correlator.repository import (
    DuplicateActiveIncidentError,
    IncidentRepository,
    IncidentUnitOfWork,
)
from incident_correlator.severity import SeverityConfig, SeverityInputs, evaluate_severity
from incident_correlator.topology import (
    TopologyConfig,
    dependencies_of,
    incidents_overlap,
    related_services,
)
from sentinelops_common.kafka import KafkaJsonProducer, RetryableError

logger = logging.getLogger("incident_correlator.processor")


class ProcessResult(StrEnum):
    CREATED = "created"
    APPENDED = "appended"
    SUPERSEDED = "superseded"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class ProcessOutcome:
    result: ProcessResult
    incident_id: str | None
    correlation_reason: str


def _new_incident_id() -> str:
    return f"inc_{secrets.token_hex(8)}"


def _now() -> datetime:
    return datetime.now(tz=UTC)


class AnomalyProcessor:
    def __init__(
        self,
        repository: IncidentRepository,
        *,
        lifecycle_producer: KafkaJsonProducer | None = None,
        incident_topic: str = "incident.events",
        correlation_config: CorrelationConfig | None = None,
        severity_config: SeverityConfig | None = None,
        topology_config: TopologyConfig | None = None,
        max_supersede_retries: int = 3,
    ) -> None:
        self._repo = repository
        self._producer = lifecycle_producer
        self._incident_topic = incident_topic
        self._corr_cfg = correlation_config or CorrelationConfig()
        self._sev_cfg = severity_config or SeverityConfig()
        self._topo_cfg = topology_config or TopologyConfig()
        self._max_retries = max_supersede_retries

    async def process(self, signal: AnomalySignal) -> ProcessOutcome:
        for attempt in range(1, self._max_retries + 1):
            try:
                return await self._process_once(signal)
            except DuplicateActiveIncidentError:
                if attempt == self._max_retries:
                    raise RetryableError(
                        f"lost the create race repeatedly for {signal.correlation_key!r}"
                    ) from None
                logger.info(
                    "lost the incident-create race; retrying",
                    extra={"correlation_key": signal.correlation_key, "attempt": attempt},
                )
        raise AssertionError("unreachable")

    async def _process_once(self, signal: AnomalySignal) -> ProcessOutcome:
        published: tuple[Incident, str] | None = None

        async with self._repo.unit_of_work() as uow:
            if await uow.evidence_exists(signal.event_id):
                logger.info(
                    "duplicate anomaly event; skipping",
                    extra={"event_id": signal.event_id, "service": signal.service},
                )
                return ProcessOutcome(ProcessResult.DUPLICATE, None, "duplicate event_id")

            active = await uow.lock_active_incident(signal.correlation_key)
            decision = decide(signal, active, self._corr_cfg)

            if decision.action is CorrelationAction.CREATE:
                incident, evidence, transition = self._new_incident(signal, decision)
                await uow.insert_incident(incident)
                await uow.add_evidence(incident.id, evidence)
                await uow.add_transition(incident.id, transition)
                published = (incident, "opened")
                outcome = ProcessOutcome(ProcessResult.CREATED, incident.id, decision.reason)
                current = incident

            elif decision.action is CorrelationAction.APPEND:
                assert active is not None
                before = active.severity
                evidence = self._evidence(signal, decision.reason)
                self._absorb(active, signal)
                await uow.update_incident(active)
                await uow.add_evidence(active.id, evidence)
                change = "severity-changed" if active.severity != before else "evidence-added"
                published = (active, change)
                outcome = ProcessOutcome(ProcessResult.APPENDED, active.id, decision.reason)
                current = active

            else:  # SUPERSEDE
                assert active is not None
                resolve_transition = self._auto_resolve(active, decision.reason)
                await uow.update_incident(active)
                await uow.add_transition(active.id, resolve_transition)

                incident, evidence, transition = self._new_incident(signal, decision)
                await uow.insert_incident(incident)
                await uow.add_evidence(incident.id, evidence)
                await uow.add_transition(incident.id, transition)
                published = (incident, "opened")
                outcome = ProcessOutcome(ProcessResult.SUPERSEDED, incident.id, decision.reason)
                current = incident

            # Phase 8: link this incident to concurrent incidents in adjacent
            # services (a dependency or a dependent). Same transaction as the
            # incident write, so a crash leaves no dangling half-link.
            await self._link_related_incidents(current, uow)

        if published is not None:
            await self._publish(*published)
        return outcome

    # --- incident construction / mutation ------------------------------
    def _evidence(self, signal: AnomalySignal, reason: str) -> EvidenceRecord:
        return EvidenceRecord(
            event_id=signal.event_id,
            detector=signal.detector,
            detector_version=signal.detector_version,
            anomaly_score=signal.anomaly_score,
            threshold=signal.threshold,
            window_start=signal.window_start,
            window_end=signal.window_end,
            signals=dict(signal.signals),
            abnormal_signals=list(signal.abnormal_signals),
            trace_id=signal.trace_id,
            occurred_at=signal.occurred_at,
            correlation_reason=reason,
        )

    def _new_incident(
        self, signal: AnomalySignal, decision: CorrelationDecision
    ) -> tuple[Incident, EvidenceRecord, StateTransition]:
        now = _now()
        incident = Incident(
            id=_new_incident_id(),
            correlation_key=signal.correlation_key,
            service=signal.service,
            environment=signal.environment,
            status=IncidentStatus.OPEN,
            severity=Severity.INFO,
            severity_reasons=[],
            title=f"Anomalous behaviour in {signal.service} ({signal.environment})",
            anomaly_count=0,
            max_anomaly_score=0.0,
            max_error_rate=0.0,
            max_latency_p95_ms=0.0,
            detector=signal.detector,
            started_at=signal.window_start,
            last_evidence_at=signal.window_end,
            created_at=now,
            updated_at=now,
        )
        self._absorb(incident, signal)
        evidence = self._evidence(signal, decision.reason)
        transition = StateTransition(
            from_status=None,
            to_status=IncidentStatus.OPEN,
            actor="system",
            reason=decision.reason,
            severity_at_transition=incident.severity,
            created_at=now,
        )
        return incident, evidence, transition

    def _absorb(self, incident: Incident, signal: AnomalySignal) -> None:
        """Fold one anomaly's signals into the incident's aggregates. O(1)."""

        incident.anomaly_count += 1
        incident.last_evidence_at = max(incident.last_evidence_at, signal.window_end)
        incident.started_at = min(incident.started_at, signal.window_start)
        incident.updated_at = _now()
        incident.max_anomaly_score = max(incident.max_anomaly_score, signal.anomaly_score)
        incident.max_error_rate = max(
            incident.max_error_rate, float(signal.signals.get("error_rate", 0.0))
        )
        incident.max_latency_p95_ms = max(
            incident.max_latency_p95_ms, float(signal.signals.get("latency_p95_ms", 0.0))
        )
        names = set(incident.abnormal_signal_names) | set(signal.abnormal_signals)
        incident.abnormal_signal_names = sorted(names)

        verdict = evaluate_severity(
            SeverityInputs(
                anomaly_count=incident.anomaly_count,
                distinct_abnormal_signals=incident.distinct_abnormal_signals,
                max_anomaly_score=incident.max_anomaly_score,
                max_error_rate=incident.max_error_rate,
                max_latency_p95_ms=incident.max_latency_p95_ms,
                duration_seconds=incident.duration_seconds,
            ),
            self._sev_cfg,
        )
        incident.severity = verdict.level
        incident.severity_reasons = verdict.reasons
        signals_text = ", ".join(incident.abnormal_signal_names) or "anomaly"
        incident.title = (
            f"{verdict.level} - {signals_text} in {incident.service} ({incident.environment})"
        )

    def _auto_resolve(self, incident: Incident, reason: str) -> StateTransition:
        now = _now()
        prev = incident.status
        incident.status = IncidentStatus.RESOLVED
        incident.resolved_at = incident.last_evidence_at
        incident.resolution = "auto:stale"
        incident.updated_at = now
        return StateTransition(
            from_status=prev,
            to_status=IncidentStatus.RESOLVED,
            actor="system",
            reason=f"auto-resolved: {reason}",
            severity_at_transition=incident.severity,
            created_at=now,
        )

    async def _link_related_incidents(self, incident: Incident, uow: IncidentUnitOfWork) -> None:
        """Find related incidents in dependent/dependency services and link them.

        Deterministic: a static dependency graph (:mod:`incident_correlator.topology`)
        + a fixed time window. The edge is always stored ``dependent -> dependency``
        so the relation graph stays acyclic.
        """

        graph = self._topo_cfg.dependency_graph
        neighbours = related_services(incident.service, graph)
        if not neighbours:
            return

        window = self._topo_cfg.correlation_window_seconds
        candidates = await uow.active_incidents_in_services(
            sorted(neighbours), incident.environment
        )
        downstream = set(dependencies_of(incident.service, graph))
        now = _now()
        for other in candidates:
            if other.id == incident.id:
                continue
            if not incidents_overlap(incident, other, window):
                continue
            if other.service in downstream:
                dependent_id, dependency_id = incident.id, other.id
                dependent_svc, dependency_svc = incident.service, other.service
            else:
                dependent_id, dependency_id = other.id, incident.id
                dependent_svc, dependency_svc = other.service, incident.service
            await uow.link_incident(
                IncidentRelation(
                    incident_id=dependent_id,
                    related_incident_id=dependency_id,
                    relation_type=IncidentRelationType.DEPENDENCY,
                    reason=(
                        f"{dependent_svc} depends on {dependency_svc}; "
                        f"concurrent incidents within {window:.0f}s"
                    ),
                    created_at=now,
                )
            )
            logger.info(
                "linked cross-service incidents",
                extra={
                    "incident_id": dependent_id,
                    "related_incident_id": dependency_id,
                    "relation": IncidentRelationType.DEPENDENCY.value,
                },
            )

    async def _publish(self, incident: Incident, change: str) -> None:
        if self._producer is None or not self._producer.ready:
            return
        try:
            await self._producer.publish(
                self._incident_topic,
                lifecycle_envelope(incident, change=change),
                key=incident.correlation_key,
            )
        except Exception:
            logger.warning(
                "failed to publish incident lifecycle event",
                extra={"incident_id": incident.id, "change": change},
            )


__all__ = [
    "AnomalyProcessor",
    "IncidentUnitOfWork",
    "ProcessOutcome",
    "ProcessResult",
]
