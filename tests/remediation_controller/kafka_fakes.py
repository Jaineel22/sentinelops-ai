"""In-memory Kafka doubles for Phase 5G lifecycle-event tests."""

from __future__ import annotations

from dataclasses import dataclass, field

from sentinelops_common.events import EventEnvelope


@dataclass
class PublishedMessage:
    topic: str
    key: str
    envelope: EventEnvelope


@dataclass
class FakeKafkaProducer:
    """Mimics :class:`sentinelops_common.kafka.KafkaJsonProducer` closely enough
    for :class:`~remediation_controller.kafka.publisher.RemediationEventPublisher`.
    Records every publish; can be told to fail."""

    started: bool = True
    fail: bool = False
    messages: list[PublishedMessage] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.started

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def publish(
        self,
        topic: str,
        envelope: EventEnvelope,
        *,
        key: str,
        extra_headers: list[tuple[str, bytes]] | None = None,
    ) -> None:
        if self.fail:
            raise RuntimeError("simulated kafka publish failure")
        self.messages.append(PublishedMessage(topic=topic, key=key, envelope=envelope))

    # convenience for assertions
    def event_types(self) -> list[str]:
        return [m.envelope.event_type for m in self.messages]

    def keys(self) -> list[str]:
        return [m.key for m in self.messages]
