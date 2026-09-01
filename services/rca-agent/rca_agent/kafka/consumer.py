"""``incident.events`` -> :class:`~rca_agent.engine.InvestigationService`.

Thin handler over ``sentinelops_common.kafka.IdempotentConsumer``:

* ``incident.opened``  -> dedupe, then ``InvestigationService.investigate``.
* ``incident.updated`` / ``incident.resolved`` -> ack and ignore (not for us).
* malformed / unsupported ``incident.opened`` -> DLQ (``MessageRejected``).
* transient failure starting the investigation -> bounded retry -> DLQ.

No LLM logic, no graph wiring, no persistence detail here — the consumer only
translates the envelope and calls the application service.
"""

from __future__ import annotations

import logging
import time

from aiokafka.structs import ConsumerRecord
from opentelemetry.propagate import extract

from rca_agent.config import Settings
from rca_agent.domain import InvestigationTrigger
from rca_agent.engine import InvestigationService
from rca_agent.kafka.events import (
    IncidentOpenedEventError,
    incident_ref_from_envelope,
    is_incident_opened,
)
from rca_agent.metrics import RcaMetrics
from sentinelops_common.events import EventEnvelope
from sentinelops_common.kafka import (
    IdempotentConsumer,
    KafkaJsonProducer,
    MessageRejected,
    RetryableError,
)
from sentinelops_common.obs import get_tracer

logger = logging.getLogger("rca_agent.kafka.consumer")


class IncidentEventConsumer:
    def __init__(
        self,
        settings: Settings,
        service: InvestigationService,
        *,
        dlq_producer: KafkaJsonProducer,
        metrics: RcaMetrics,
    ) -> None:
        self._service = service
        self._metrics = metrics
        self._consumer = IdempotentConsumer(
            bootstrap_servers=settings.kafka.bootstrap_servers,
            topic=settings.kafka.incident_topic,
            group_id=settings.kafka.consumer_group,
            dlq_producer=dlq_producer,
            dlq_topic=settings.kafka.incident_dlq_topic,
            max_retries=settings.kafka.max_retries,
            retry_backoff_seconds=settings.kafka.retry_backoff_seconds,
        )

    @property
    def healthy(self) -> bool:
        return self._consumer.healthy

    @property
    def processed(self) -> int:
        return self._consumer.processed

    async def start(self) -> None:
        await self._consumer.start(self.handle)

    async def stop(self) -> None:
        await self._consumer.stop()

    async def handle(self, envelope: EventEnvelope, record: ConsumerRecord) -> None:
        """Process one lifecycle event. The public handler wired into
        :class:`~sentinelops_common.kafka.IdempotentConsumer` — raises
        :class:`MessageRejected` (-> DLQ) or :class:`RetryableError` (-> bounded
        retry) per that contract; returns cleanly for events it ignores."""
        self._metrics.events_consumed.add(1, {"event_type": envelope.event_type})

        if not is_incident_opened(envelope):
            logger.debug(
                "ignoring non-opened incident lifecycle event",
                extra={"event_id": envelope.event_id, "event_type": envelope.event_type},
            )
            return

        tracer = get_tracer()
        ctx = extract({k: v.decode("utf-8") for k, v in (record.headers or [])})
        with tracer.start_as_current_span("rca.consume_incident_opened", context=ctx) as span:
            span.set_attribute("messaging.system", "kafka")
            span.set_attribute("event.id", envelope.event_id)
            try:
                ref = incident_ref_from_envelope(envelope)
            except IncidentOpenedEventError as exc:
                self._metrics.events_rejected.add(1, {"reason": "malformed"})
                raise MessageRejected(str(exc)) from exc

            span.set_attribute("incident.id", ref.incident_id)
            span.set_attribute("service.name", ref.service)

            existing = await self._service.get_existing_investigation(ref.incident_id)
            if existing is not None:
                self._metrics.duplicate_events.add(1)
                span.set_attribute("rca.duplicate", True)
                logger.info(
                    "incident.opened already handled; skipping",
                    extra={
                        "event_id": envelope.event_id,
                        "incident_id": ref.incident_id,
                        "investigation_id": existing.id,
                        "investigation_status": str(existing.status),
                    },
                )
                return

            started = time.perf_counter()
            self._metrics.record_started(str(InvestigationTrigger.EVENT))
            try:
                outcome = await self._service.investigate(
                    ref.incident_id, trigger=InvestigationTrigger.EVENT
                )
            except Exception as exc:  # engine errors are already absorbed; this is infra
                logger.exception(
                    "failed to start investigation for incident.opened",
                    extra={"event_id": envelope.event_id, "incident_id": ref.incident_id},
                )
                raise RetryableError(f"could not run investigation: {type(exc).__name__}") from exc

            status = outcome.investigation.status
            span.set_attribute("investigation.id", outcome.investigation.id)
            span.set_attribute("investigation.status", str(status))
            if not outcome.already_running:
                self._metrics.record_completed(
                    status, duration_seconds=time.perf_counter() - started
                )
            logger.info(
                "incident.opened investigated",
                extra={
                    "event_id": envelope.event_id,
                    "incident_id": ref.incident_id,
                    "investigation_id": outcome.investigation.id,
                    "status": str(status),
                    "already_running": outcome.already_running,
                    "has_root_cause": outcome.report is not None
                    and outcome.report.root_cause is not None,
                },
            )
