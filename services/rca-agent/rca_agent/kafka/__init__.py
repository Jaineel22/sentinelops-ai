"""Kafka integration: ``incident.opened`` (Phase 3) -> InvestigationService (4E).

The consume loop itself lives in ``sentinelops_common.kafka.IdempotentConsumer``;
this package is only the handler — parse the lifecycle envelope, dedupe a
redelivered event, and invoke the application service. No LLM logic here.
"""

from __future__ import annotations

from rca_agent.kafka.consumer import IncidentEventConsumer
from rca_agent.kafka.events import IncidentOpenedEventError, incident_ref_from_envelope

__all__ = [
    "IncidentEventConsumer",
    "IncidentOpenedEventError",
    "incident_ref_from_envelope",
]
