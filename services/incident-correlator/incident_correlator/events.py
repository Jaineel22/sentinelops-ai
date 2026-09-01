"""Inbound / outbound event translation.

Inbound:  Kafka ``anomaly.detected`` envelope  ->  :class:`AnomalySignal`.
Outbound: an :class:`~incident_correlator.domain.Incident` snapshot  ->
``incident.*`` lifecycle envelope.

All parsing failures raise :class:`AnomalyEventError`, which the processor maps
to the DLQ path.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import ValidationError

from incident_correlator import SERVICE_NAME
from incident_correlator.domain import AnomalySignal, Incident
from sentinelops_common.contracts import (
    ANOMALY_DETECTED,
    ANOMALY_DETECTED_VERSION,
    INCIDENT_LIFECYCLE_VERSION,
    AnomalyDetectedV1,
    IncidentLifecycleV1,
)
from sentinelops_common.events import EventEnvelope


class AnomalyEventError(ValueError):
    """An ``anomaly.detected`` envelope could not be turned into a signal."""


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def anomaly_signal_from_envelope(envelope: EventEnvelope) -> AnomalySignal:
    if envelope.event_type != ANOMALY_DETECTED:
        raise AnomalyEventError(f"unexpected event_type {envelope.event_type!r}")
    if envelope.event_version != ANOMALY_DETECTED_VERSION:
        raise AnomalyEventError(
            f"unsupported {ANOMALY_DETECTED} version {envelope.event_version}"
            f" (this service speaks v{ANOMALY_DETECTED_VERSION})"
        )
    try:
        payload = AnomalyDetectedV1.model_validate(envelope.payload)
    except ValidationError as exc:
        raise AnomalyEventError(f"payload does not match {ANOMALY_DETECTED} v1: {exc}") from exc

    if not payload.is_anomaly:
        raise AnomalyEventError("event is not flagged as an anomaly (is_anomaly=false)")

    try:
        return AnomalySignal(
            event_id=envelope.event_id,
            detector=payload.detector,
            detector_version=payload.detector_version,
            service=payload.service,
            environment=payload.environment,
            window_start=_parse_ts(payload.window_start),
            window_end=_parse_ts(payload.window_end),
            anomaly_score=payload.anomaly_score,
            threshold=payload.threshold,
            signals=payload.signals,
            abnormal_signals=list(payload.abnormal_signals),
            trace_id=envelope.trace_id,
            occurred_at=envelope.occurred_at,
        )
    except ValueError as exc:
        raise AnomalyEventError(f"bad field value: {exc}") from exc


_CHANGE_TO_EVENT_TYPE = {
    "opened": "incident.opened",
    "resolved": "incident.resolved",
}


def lifecycle_envelope(incident: Incident, *, change: str) -> EventEnvelope:
    event_type = _CHANGE_TO_EVENT_TYPE.get(change, "incident.updated")
    payload = IncidentLifecycleV1(
        incident_id=incident.id,
        correlation_key=incident.correlation_key,
        service=incident.service,
        environment=incident.environment,
        status=str(incident.status),
        severity=str(incident.severity),
        anomaly_count=incident.anomaly_count,
        title=incident.title,
        started_at=incident.started_at.isoformat(),
        updated_at=incident.updated_at.isoformat(),
        change=change,
    )
    return EventEnvelope(
        event_type=event_type,
        event_version=INCIDENT_LIFECYCLE_VERSION,
        source=SERVICE_NAME,
        trace_id=None,
        payload=payload.model_dump(),
    )
