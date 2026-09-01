"""Kafka helpers shared by SentinelOps services.

* :class:`KafkaJsonProducer` — a thin wrapper over ``AIOKafkaProducer`` with the
  reliability settings the platform standardises on (``acks=all``, idempotent).
* :class:`IdempotentConsumer` — a manual-commit consume loop:

      poll -> decode -> handler(envelope, record) -> commit offset

  The offset is committed **only after the handler returns**, so a crash before
  the handler finishes replays the message (at-least-once). Handlers must be
  idempotent. Malformed messages and messages that keep failing are routed to a
  dead-letter topic rather than blocking the partition forever.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaError
from aiokafka.structs import ConsumerRecord, TopicPartition

from sentinelops_common.events import EventDecodeError, EventEnvelope, parse_envelope

logger = logging.getLogger("sentinelops_common.kafka")


async def ensure_topics(
    bootstrap_servers: str,
    topics: list[str],
    *,
    client_id: str,
    num_partitions: int = 1,
    replication_factor: int = 1,
) -> None:
    """Best-effort idempotent topic creation for local / dev runs.

    A real deployment provisions topics out of band (IaC / ops runbook); this
    only exists so ``docker compose up`` works without a manual
    ``kafka-topics.sh`` step. Failures are logged, not raised.
    """

    from aiokafka.admin import AIOKafkaAdminClient, NewTopic
    from aiokafka.errors import TopicAlreadyExistsError

    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap_servers, client_id=f"{client_id}-admin")
    try:
        await admin.start()
        for topic in topics:
            try:
                await admin.create_topics(
                    [
                        NewTopic(
                            topic,
                            num_partitions=num_partitions,
                            replication_factor=replication_factor,
                        )
                    ]
                )
                logger.info("created kafka topic", extra={"topic": topic, "outcome": "created"})
            except TopicAlreadyExistsError:
                logger.debug("kafka topic exists", extra={"topic": topic})
    except Exception:
        logger.warning("could not ensure kafka topics %s", topics, exc_info=True)
    finally:
        with contextlib.suppress(Exception):
            await admin.close()


class RetryableError(RuntimeError):
    """Raised by a handler for a transient failure (DB down, downstream timeout).

    The message is retried with backoff; it is never dropped silently.
    """


class MessageRejected(ValueError):
    """Raised by a handler for a message it understands but cannot accept
    (unknown schema version, business-rule violation). Routed straight to the
    DLQ — retrying would not help — without a stack trace."""


class KafkaJsonProducer:
    def __init__(self, bootstrap_servers: str, *, client_id: str) -> None:
        self._bootstrap = bootstrap_servers
        self._client_id = client_id
        self._producer: AIOKafkaProducer | None = None

    @property
    def ready(self) -> bool:
        return self._producer is not None

    async def start(self) -> None:
        if self._producer is not None:
            return
        producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap,
            client_id=self._client_id,
            acks="all",
            enable_idempotence=True,
        )
        await producer.start()
        self._producer = producer

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def publish(
        self,
        topic: str,
        envelope: EventEnvelope,
        *,
        key: str,
        extra_headers: list[tuple[str, bytes]] | None = None,
    ) -> None:
        if self._producer is None:
            raise RuntimeError("producer not started")
        headers = [*envelope.kafka_headers(), *(extra_headers or [])]
        try:
            await self._producer.send_and_wait(
                topic, key=key.encode("utf-8"), value=envelope.to_json_bytes(), headers=headers
            )
        except KafkaError as exc:  # pragma: no cover - needs a broken broker
            raise RetryableError(f"kafka publish to {topic!r} failed: {exc}") from exc

    async def publish_raw(
        self, topic: str, value: bytes, *, key: bytes | None, headers: list[tuple[str, bytes]]
    ) -> None:
        if self._producer is None:
            raise RuntimeError("producer not started")
        await self._producer.send_and_wait(topic, key=key, value=value, headers=headers)


Handler = Callable[[EventEnvelope, ConsumerRecord], Awaitable[None]]


class IdempotentConsumer:
    def __init__(
        self,
        *,
        bootstrap_servers: str,
        topic: str,
        group_id: str,
        dlq_producer: KafkaJsonProducer,
        dlq_topic: str,
        max_retries: int = 3,
        retry_backoff_seconds: float = 2.0,
        poll_timeout_ms: int = 1000,
    ) -> None:
        self._bootstrap = bootstrap_servers
        self._topic = topic
        self._group_id = group_id
        self._dlq = dlq_producer
        self._dlq_topic = dlq_topic
        self._max_retries = max_retries
        self._backoff = retry_backoff_seconds
        self._poll_timeout_ms = poll_timeout_ms

        self._consumer: AIOKafkaConsumer | None = None
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.healthy = False
        self.processed = 0
        self.rejected = 0

    async def start(self, handler: Handler) -> None:
        self._consumer = AIOKafkaConsumer(
            self._topic,
            bootstrap_servers=self._bootstrap,
            group_id=self._group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )
        await self._consumer.start()
        self.healthy = True
        self._task = asyncio.create_task(self._run(handler))

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None
        self.healthy = False

    async def _run(self, handler: Handler) -> None:
        assert self._consumer is not None
        try:
            while not self._stop.is_set():
                batch = await self._consumer.getmany(
                    timeout_ms=self._poll_timeout_ms, max_records=50
                )
                for tp, records in batch.items():
                    for record in records:
                        if self._stop.is_set():
                            return
                        await self._process_one(handler, tp, record)
        finally:
            self.healthy = False

    async def _process_one(
        self, handler: Handler, tp: TopicPartition, record: ConsumerRecord
    ) -> None:
        assert self._consumer is not None
        try:
            envelope = parse_envelope(record.value)
        except EventDecodeError as exc:
            await self._to_dlq(record, reason=f"decode: {exc}")
            await self._commit(tp, record)
            self.rejected += 1
            return

        for attempt in range(1, self._max_retries + 1):
            try:
                await handler(envelope, record)
                await self._commit(tp, record)
                self.processed += 1
                return
            except MessageRejected as exc:
                logger.warning(
                    "message rejected; sending to DLQ",
                    extra={"event_id": envelope.event_id, "reason": str(exc)},
                )
                await self._to_dlq(record, reason=f"rejected: {exc}")
                await self._commit(tp, record)
                self.rejected += 1
                return
            except RetryableError as exc:
                logger.warning(
                    "retryable failure processing event",
                    extra={
                        "event_id": envelope.event_id,
                        "attempt": attempt,
                        "max_retries": self._max_retries,
                        "error": str(exc),
                    },
                )
                if attempt == self._max_retries:
                    await self._to_dlq(record, reason=f"retries exhausted: {exc}")
                    await self._commit(tp, record)
                    self.rejected += 1
                    return
                await asyncio.sleep(self._backoff * attempt)
            except Exception as exc:
                logger.exception(
                    "unexpected failure processing event; sending to DLQ",
                    extra={"event_id": envelope.event_id, "error": str(exc)},
                )
                await self._to_dlq(record, reason=f"unexpected: {type(exc).__name__}: {exc}")
                await self._commit(tp, record)
                self.rejected += 1
                return

    async def _commit(self, tp: TopicPartition, record: ConsumerRecord) -> None:
        assert self._consumer is not None
        await self._consumer.commit({tp: record.offset + 1})

    async def _to_dlq(self, record: ConsumerRecord, *, reason: str) -> None:
        headers = [
            *[(k, v) for k, v in (record.headers or [])],
            ("dlq-reason", reason.encode("utf-8")[:512]),
            ("dlq-source-topic", self._topic.encode("utf-8")),
        ]
        try:
            await self._dlq.publish_raw(
                self._dlq_topic, record.value or b"", key=record.key, headers=headers
            )
        except Exception:
            logger.exception("failed to write to DLQ", extra={"dlq_topic": self._dlq_topic})
