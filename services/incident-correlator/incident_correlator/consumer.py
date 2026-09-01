"""Kafka wiring: ``anomaly.events`` -> :class:`AnomalyProcessor`.

The idempotent consume loop lives in ``sentinelops_common``; this module is just
the handler: translate the envelope, process it, record metrics, and let
malformed / unknown-version events fall through to the DLQ.
"""

from __future__ import annotations

import logging
import time

from aiokafka.structs import ConsumerRecord
from opentelemetry.propagate import extract

from incident_correlator.config import Settings
from incident_correlator.events import AnomalyEventError, anomaly_signal_from_envelope
from incident_correlator.metrics import CorrelatorMetrics
from incident_correlator.processor import AnomalyProcessor
from sentinelops_common.events import EventEnvelope
from sentinelops_common.kafka import (
    IdempotentConsumer,
    KafkaJsonProducer,
    MessageRejected,
    RetryableError,
)
from sentinelops_common.obs import get_tracer

logger = logging.getLogger("incident_correlator.consumer")


class AnomalyConsumer:
    def __init__(
        self,
        settings: Settings,
        processor: AnomalyProcessor,
        *,
        dlq_producer: KafkaJsonProducer,
        metrics: CorrelatorMetrics,
    ) -> None:
        self._processor = processor
        self._metrics = metrics
        self._consumer = IdempotentConsumer(
            bootstrap_servers=settings.kafka.bootstrap_servers,
            topic=settings.kafka.anomaly_topic,
            group_id=settings.kafka.consumer_group,
            dlq_producer=dlq_producer,
            dlq_topic=settings.kafka.anomaly_dlq_topic,
            max_retries=settings.kafka.max_retries,
            retry_backoff_seconds=settings.kafka.retry_backoff_seconds,
        )

    @property
    def healthy(self) -> bool:
        return self._consumer.healthy

    async def start(self) -> None:
        await self._consumer.start(self._handle)

    async def stop(self) -> None:
        await self._consumer.stop()

    async def _handle(self, envelope: EventEnvelope, record: ConsumerRecord) -> None:
        tracer = get_tracer()
        ctx = extract({k: v.decode("utf-8") for k, v in (record.headers or [])})
        started = time.perf_counter()
        with tracer.start_as_current_span("incident.process_anomaly", context=ctx) as span:
            span.set_attribute("messaging.system", "kafka")
            span.set_attribute("event.id", envelope.event_id)
            try:
                signal = anomaly_signal_from_envelope(envelope)
            except AnomalyEventError as exc:
                self._metrics.anomalies_rejected.add(1, {"reason": "invalid"})
                raise MessageRejected(str(exc)) from exc

            span.set_attribute("service.name", signal.service)
            span.set_attribute("correlation.key", signal.correlation_key)
            try:
                outcome = await self._processor.process(signal)
            except RetryableError:
                self._metrics.correlation_failures.add(1)
                raise
            except Exception:
                self._metrics.correlation_failures.add(1)
                raise

            span.set_attribute("incident.outcome", outcome.result.value)
            if outcome.incident_id:
                span.set_attribute("incident.id", outcome.incident_id)
            self._metrics.record_outcome(outcome.result)
            self._metrics.processing_latency.record(time.perf_counter() - started)
            logger.info(
                "anomaly processed",
                extra={
                    "event_id": envelope.event_id,
                    "service": signal.service,
                    "correlation_key": signal.correlation_key,
                    "outcome": outcome.result.value,
                    "incident_id": outcome.incident_id,
                    "correlation_reason": outcome.correlation_reason,
                },
            )
