"""Inbound event translation: Kafka ``incident.opened`` envelope -> incident ref.

The rca-agent reacts to exactly one Phase 3 lifecycle event — ``incident.opened``
(``sentinelops_common.contracts.INCIDENT_OPENED``). ``incident.updated`` /
``incident.resolved`` travel on the same topic and are ignored (not an error).

Everything the model will later see about the incident is re-fetched over the
Incident API during the investigation; this payload is used only to learn *which*
incident to investigate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import ValidationError

from rca_agent.tools.contracts import INCIDENT_ID_RE
from sentinelops_common.contracts import (
    INCIDENT_LIFECYCLE_VERSION,
    INCIDENT_OPENED,
    IncidentLifecycleV1,
)
from sentinelops_common.events import EventEnvelope

_INCIDENT_ID = re.compile(INCIDENT_ID_RE)


class IncidentOpenedEventError(ValueError):
    """An ``incident.opened`` envelope could not be turned into an incident ref.

    Raised only for events that *claim* to be ``incident.opened`` but are
    malformed / unsupported — the consumer routes these to the DLQ. An event of a
    different type is not an error (see :func:`is_incident_opened`).
    """


@dataclass(frozen=True)
class IncidentRef:
    """The minimum needed to start an investigation."""

    incident_id: str
    service: str
    environment: str
    severity: str
    correlation_key: str


def is_incident_opened(envelope: EventEnvelope) -> bool:
    return envelope.event_type == INCIDENT_OPENED


def incident_ref_from_envelope(envelope: EventEnvelope) -> IncidentRef:
    """Validate an ``incident.opened`` envelope and extract the incident ref.

    Raises :class:`IncidentOpenedEventError` for a malformed / unsupported
    ``incident.opened`` event. The caller must have already checked
    :func:`is_incident_opened`.
    """

    if envelope.event_type != INCIDENT_OPENED:  # pragma: no cover - guarded by caller
        raise IncidentOpenedEventError(f"not an {INCIDENT_OPENED} event: {envelope.event_type!r}")
    if envelope.event_version != INCIDENT_LIFECYCLE_VERSION:
        raise IncidentOpenedEventError(
            f"unsupported {INCIDENT_OPENED} version {envelope.event_version} "
            f"(this service speaks v{INCIDENT_LIFECYCLE_VERSION})"
        )
    try:
        payload = IncidentLifecycleV1.model_validate(envelope.payload)
    except ValidationError as exc:
        raise IncidentOpenedEventError(
            f"payload does not match {INCIDENT_OPENED} v1: {exc}"
        ) from exc

    if not _INCIDENT_ID.match(payload.incident_id):
        raise IncidentOpenedEventError(f"incident_id {payload.incident_id!r} is not a valid id")

    return IncidentRef(
        incident_id=payload.incident_id,
        service=payload.service,
        environment=payload.environment,
        severity=payload.severity,
        correlation_key=payload.correlation_key,
    )
