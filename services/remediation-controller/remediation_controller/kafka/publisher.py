"""Best-effort publication of remediation lifecycle events (Phase 5G, ADR-030).

Consistency model (deliberately simple, matching ``incident.events`` / ADR-016):

* Every lifecycle transition is recorded in ``remediation_audit_events`` **in the
  same PostgreSQL transaction** as the state change (Phase 5E). That trail — not
  Kafka — is the durable, ordered, immutable record.
* **After** that transaction commits, :class:`RemediationEventPublisher` mirrors
  the committed audit events onto ``remediation.events``. A publish failure is
  counted + logged and never rolls back or fails the API call.
* Key = ``remediation_id`` -> every event for one remediation stays ordered
  within a partition.
* ``event_id`` is derived deterministically from the audit-row id, so a consumer
  keying on ``event_id`` deduplicates a republish after a restart.

Limitation: a crash / broker outage between commit and publish drops that event
from Kafka. The database + audit trail remain correct and authoritative; a
consumer must reconcile against the Remediation API, never treat this stream as
the source of truth. A transactional outbox was considered and deferred
(ADR-030) — the append-only audit table already provides the durable backing a
future relay could poll.

This module publishes only. It never consumes, and a Kafka message is never
interpreted as an instruction (ADR-030, ADR-003).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Protocol

from remediation_controller.audit.model import RemediationAuditEvent
from remediation_controller.kafka.events import lifecycle_envelope
from remediation_controller.metrics import RemediationMetrics
from sentinelops_common.events import EventEnvelope

logger = logging.getLogger("remediation_controller.kafka.publisher")


class _Producer(Protocol):
    """The slice of ``sentinelops_common.kafka.KafkaJsonProducer`` the publisher
    needs. A test double can satisfy it structurally."""

    @property
    def ready(self) -> bool: ...

    async def publish(
        self,
        topic: str,
        envelope: EventEnvelope,
        *,
        key: str,
        extra_headers: list[tuple[str, bytes]] | None = ...,
    ) -> None: ...


class RemediationEventPublisher:
    """Wraps a :class:`KafkaJsonProducer`; emits one lifecycle event per
    publishable committed audit fact. Safe to call when the producer is not
    started — it is then a no-op (the audit trail is still the record)."""

    def __init__(
        self,
        producer: _Producer | None,
        *,
        topic: str,
        source: str,
        metrics: RemediationMetrics,
    ) -> None:
        self._producer = producer
        self._topic = topic
        self._source = source
        self._metrics = metrics

    @property
    def ready(self) -> bool:
        return self._producer is not None and self._producer.ready

    @property
    def topic(self) -> str:
        return self._topic

    async def publish_audit_events(self, events: Sequence[RemediationAuditEvent]) -> None:
        """Best-effort: mirror each publishable audit event onto the lifecycle
        topic. Never raises — a failure is recorded and the next event is still
        attempted."""

        if self._producer is None or not self._producer.ready:
            return
        for event in events:
            envelope = lifecycle_envelope(event, source=self._source)
            if envelope is None:
                continue
            started = time.perf_counter()
            try:
                await self._producer.publish(self._topic, envelope, key=event.remediation_id)
            except Exception:
                self._metrics.record_event_publish_failure(envelope.event_type)
                logger.warning(
                    "failed to publish remediation lifecycle event",
                    extra={
                        "remediation_id": event.remediation_id,
                        "incident_id": event.incident_id,
                        "event_type": envelope.event_type,
                        "audit_id": event.audit_id,
                        "correlation_id": event.correlation_id,
                    },
                )
                continue
            self._metrics.record_event_published(
                envelope.event_type, duration_seconds=time.perf_counter() - started
            )
            logger.info(
                "published remediation lifecycle event",
                extra={
                    "remediation_id": event.remediation_id,
                    "incident_id": event.incident_id,
                    "event_type": envelope.event_type,
                    "event_id": envelope.event_id,
                    "audit_id": event.audit_id,
                    "correlation_id": event.correlation_id,
                },
            )


__all__ = ["RemediationEventPublisher"]
