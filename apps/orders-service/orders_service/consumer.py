"""Demo consumer for ``orders.events``.

Its only job is to prove the path works end to end: producer -> Kafka ->
consumer. It logs each event (structured, continuing the trace carried in the
message headers) and keeps a running count.

This is **not** a real event-processing service. Incident correlation, ML
scoring, and the rest consume this topic in later phases with their own,
properly designed consumers.

Run: ``python -m orders_service.consumer``
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal

from aiokafka import AIOKafkaConsumer
from aiokafka.structs import ConsumerRecord
from opentelemetry.propagate import extract

from orders_service.config import get_settings
from orders_service.logging_setup import configure_logging
from orders_service.telemetry import configure_telemetry, get_tracer, shutdown_telemetry

logger = logging.getLogger("orders_service.consumer")


def _carrier(record: ConsumerRecord) -> dict[str, str]:
    return {key: value.decode("utf-8") for key, value in (record.headers or [])}


async def consume() -> None:
    settings = get_settings()
    configure_logging(env=settings.app.env, level=settings.app.log_level)
    configure_telemetry(settings)
    tracer = get_tracer()

    consumer: AIOKafkaConsumer = AIOKafkaConsumer(
        settings.kafka.orders_topic,
        bootstrap_servers=settings.kafka.bootstrap_servers,
        client_id=f"{settings.kafka.client_id}-demo-consumer",
        group_id="orders-demo-consumer",
        auto_offset_reset="earliest",
        enable_auto_commit=True,
    )
    await consumer.start()
    logger.info(
        "demo consumer started",
        extra={"topic": settings.kafka.orders_topic, "outcome": "ready"},
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # add_signal_handler is POSIX-only
            loop.add_signal_handler(sig, stop.set)

    received = 0
    try:
        while not stop.is_set():
            batch = await consumer.getmany(timeout_ms=1000)
            for records in batch.values():
                for record in records:
                    received += 1
                    ctx = extract(_carrier(record))
                    with tracer.start_as_current_span("orders.consume_event", context=ctx):
                        _log_event(record, received)
    finally:
        await consumer.stop()
        shutdown_telemetry()
        logger.info("demo consumer stopped", extra={"outcome": "stopped", "received": received})


def _log_event(record: ConsumerRecord, received: int) -> None:
    try:
        envelope = json.loads(record.value)
    except (json.JSONDecodeError, TypeError):
        logger.warning("received non-JSON message", extra={"outcome": "bad_message"})
        return
    logger.info(
        "order event received",
        extra={
            "outcome": "consumed",
            "event_id": envelope.get("event_id"),
            "event_type": envelope.get("event_type"),
            "kafka_offset": record.offset,
            "kafka_partition": record.partition,
            "received_total": received,
        },
    )


def main() -> None:
    asyncio.run(consume())


if __name__ == "__main__":
    main()
