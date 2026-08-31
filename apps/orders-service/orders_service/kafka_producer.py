"""Kafka event publisher.

Design choices (see ADR-010):

* **Synchronous, fail-closed.** ``POST /orders`` awaits the publish and returns
  an error if it cannot confirm the event was written. An order that downstream
  systems never hear about is worse, for this platform, than a failed request.
* **Bounded.** ``acks=all`` + idempotent producer + a hard ``publish_timeout``
  so a slow/unavailable broker cannot hang a request indefinitely.
* **No outbox.** A transactional outbox is the right long-term answer and is
  explicitly out of scope for Phase 1.

Kafka logic lives here, not in route handlers. The app depends on the
``EventPublisher`` protocol so tests can substitute an in-memory fake.
"""

from __future__ import annotations

import asyncio
import logging
from types import TracebackType
from typing import Protocol

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError

from orders_service.config import KafkaSettings
from orders_service.events import EventEnvelope

logger = logging.getLogger("orders_service.kafka")


class PublishError(RuntimeError):
    """Publishing the event to Kafka failed or timed out."""


class EventPublisher(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def publish(
        self, envelope: EventEnvelope, *, key: str, headers: list[tuple[str, bytes]]
    ) -> None: ...

    @property
    def ready(self) -> bool: ...


class AIOKafkaEventPublisher:
    def __init__(self, settings: KafkaSettings) -> None:
        self._settings = settings
        self._producer: AIOKafkaProducer | None = None
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    async def start(self) -> None:
        if self._producer is not None:
            return
        if self._settings.auto_create_topic:
            await self._ensure_topic()
        producer = AIOKafkaProducer(
            bootstrap_servers=self._settings.bootstrap_servers,
            client_id=self._settings.client_id,
            acks=self._settings.acks,
            enable_idempotence=self._settings.enable_idempotence,
            request_timeout_ms=self._settings.request_timeout_ms,
        )
        await producer.start()
        self._producer = producer
        self._ready = True
        logger.info(
            "kafka producer started",
            extra={"outcome": "ready", "topic": self._settings.orders_topic},
        )

    async def stop(self) -> None:
        self._ready = False
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None
            logger.info("kafka producer stopped", extra={"outcome": "stopped"})

    async def publish(
        self, envelope: EventEnvelope, *, key: str, headers: list[tuple[str, bytes]]
    ) -> None:
        if self._producer is None:
            raise PublishError("producer is not started")
        try:
            send = self._producer.send_and_wait(
                self._settings.orders_topic,
                key=key.encode("utf-8"),
                value=envelope.to_json_bytes(),
                headers=headers,
            )
            await asyncio.wait_for(send, timeout=self._settings.publish_timeout_seconds)
        except (KafkaError, TimeoutError) as exc:
            raise PublishError(str(exc)) from exc

    async def _ensure_topic(self) -> None:
        """Best-effort idempotent topic creation for local/dev runs.

        A real deployment provisions topics out of band; this only exists so a
        developer can `docker compose up` (or run against a bare broker) without
        a manual `kafka-topics.sh` step.
        """

        from aiokafka.admin import AIOKafkaAdminClient, NewTopic
        from aiokafka.errors import TopicAlreadyExistsError

        admin = AIOKafkaAdminClient(
            bootstrap_servers=self._settings.bootstrap_servers,
            client_id=f"{self._settings.client_id}-admin",
        )
        await admin.start()
        try:
            await admin.create_topics(
                [
                    NewTopic(
                        name=self._settings.orders_topic,
                        num_partitions=self._settings.topic_partitions,
                        replication_factor=self._settings.topic_replication_factor,
                    )
                ]
            )
            logger.info(
                "created kafka topic",
                extra={"topic": self._settings.orders_topic, "outcome": "created"},
            )
        except TopicAlreadyExistsError:
            logger.debug("kafka topic already exists", extra={"topic": self._settings.orders_topic})
        finally:
            await admin.close()


class InMemoryEventPublisher:
    """Test/dev double. Records everything it is asked to publish."""

    def __init__(self) -> None:
        self.published: list[tuple[EventEnvelope, str, list[tuple[str, bytes]]]] = []
        self._ready = False
        self.fail_next = False

    @property
    def ready(self) -> bool:
        return self._ready

    async def start(self) -> None:
        self._ready = True

    async def stop(self) -> None:
        self._ready = False

    async def publish(
        self, envelope: EventEnvelope, *, key: str, headers: list[tuple[str, bytes]]
    ) -> None:
        if self.fail_next:
            self.fail_next = False
            raise PublishError("in-memory publisher: forced failure")
        self.published.append((envelope, key, headers))

    async def __aenter__(self) -> InMemoryEventPublisher:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.stop()
