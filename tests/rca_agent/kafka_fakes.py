"""Builders for ``incident.*`` Kafka fixtures (Sub-phase 4E). Not collected."""

from __future__ import annotations

from typing import Any

from sentinelops_common.contracts import (
    INCIDENT_LIFECYCLE_VERSION,
    INCIDENT_OPENED,
    IncidentLifecycleV1,
)
from sentinelops_common.events import EventEnvelope
from tests.rca_agent.incident_api_fakes import INCIDENT_ID


class FakeRecord:
    """The bits of ``aiokafka.structs.ConsumerRecord`` the handler touches."""

    def __init__(self, *, headers: list[tuple[str, bytes]] | None = None) -> None:
        self.headers = headers or []
        self.value = b""
        self.key = None
        self.offset = 0


def incident_lifecycle_envelope(
    *,
    change: str = "opened",
    event_type: str = INCIDENT_OPENED,
    event_version: int = INCIDENT_LIFECYCLE_VERSION,
    incident_id: str = INCIDENT_ID,
    service: str = "orders-service",
    environment: str = "development",
    severity: str = "HIGH",
    payload_overrides: dict[str, Any] | None = None,
    raw_payload: dict[str, Any] | None = None,
) -> EventEnvelope:
    if raw_payload is not None:
        payload = raw_payload
    else:
        payload = IncidentLifecycleV1(
            incident_id=incident_id,
            correlation_key=f"{service}:{environment}",
            service=service,
            environment=environment,
            status="OPEN",
            severity=severity,
            anomaly_count=3,
            title=f"{severity} - error_rate in {service} ({environment})",
            started_at="2026-09-01T12:00:00+00:00",
            updated_at="2026-09-01T12:05:00+00:00",
            change=change,
        ).model_dump()
        if payload_overrides:
            payload.update(payload_overrides)
    return EventEnvelope(
        event_type=event_type,
        event_version=event_version,
        source="incident-correlator",
        payload=payload,
    )
