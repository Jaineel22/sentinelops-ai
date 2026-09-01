"""``IdempotentConsumer._process_one`` routing — no real broker.

The consume *loop* (rebalancing, offsets) is covered by the ``-m integration``
end-to-end test; here we pin the decision logic: decode error -> DLQ + commit,
retryable -> retry then DLQ, unexpected -> DLQ + commit, success -> commit only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from sentinelops_common.events import EventEnvelope
from sentinelops_common.kafka import IdempotentConsumer, KafkaJsonProducer, RetryableError


@dataclass
class _FakeRecord:
    value: bytes
    key: bytes | None = None
    offset: int = 7
    headers: list[tuple[str, bytes]] = field(default_factory=list)


class _FakeInnerConsumer:
    def __init__(self) -> None:
        self.commits: list[dict[Any, int]] = []

    async def commit(self, offsets: dict[Any, int]) -> None:
        self.commits.append(offsets)


class _FakeDlq(KafkaJsonProducer):
    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes]] = []

    async def publish_raw(
        self, topic: str, value: bytes, *, key: bytes | None, headers: list[tuple[str, bytes]]
    ) -> None:
        self.sent.append((topic, value))


def _make_consumer() -> tuple[IdempotentConsumer, _FakeInnerConsumer, _FakeDlq]:
    dlq = _FakeDlq()
    c = IdempotentConsumer(
        bootstrap_servers="unused",
        topic="anomaly.events",
        group_id="g",
        dlq_producer=dlq,
        dlq_topic="anomaly.events.dlq",
        max_retries=3,
        retry_backoff_seconds=0.0,
    )
    inner = _FakeInnerConsumer()
    c._consumer = inner
    return c, inner, dlq


def _good_bytes() -> bytes:
    return EventEnvelope(
        event_type="anomaly.detected", event_version=1, source="anomaly-detector", payload={}
    ).to_json_bytes()


async def test_success_commits_once() -> None:
    c, inner, dlq = _make_consumer()
    seen: list[str] = []

    async def handler(ev: EventEnvelope, rec: object) -> None:
        seen.append(ev.event_id)

    await c._process_one(handler, "tp", _FakeRecord(_good_bytes()))
    assert len(seen) == 1
    assert inner.commits == [{"tp": 8}]
    assert dlq.sent == []
    assert c.processed == 1


async def test_decode_error_goes_to_dlq_and_commits() -> None:
    c, inner, dlq = _make_consumer()

    async def handler(ev: EventEnvelope, rec: object) -> None:  # pragma: no cover
        raise AssertionError("handler must not run on a malformed message")

    await c._process_one(handler, "tp", _FakeRecord(b"\x00garbage"))
    assert len(dlq.sent) == 1 and dlq.sent[0][0] == "anomaly.events.dlq"
    assert inner.commits == [{"tp": 8}]
    assert c.rejected == 1


async def test_retryable_retries_then_dlq() -> None:
    c, inner, dlq = _make_consumer()
    calls = 0

    async def handler(ev: EventEnvelope, rec: object) -> None:
        nonlocal calls
        calls += 1
        raise RetryableError("db down")

    await c._process_one(handler, "tp", _FakeRecord(_good_bytes()))
    assert calls == 3  # max_retries
    assert len(dlq.sent) == 1
    assert inner.commits == [{"tp": 8}]
    assert c.rejected == 1


async def test_retryable_then_success_no_dlq() -> None:
    c, inner, dlq = _make_consumer()
    calls = 0

    async def handler(ev: EventEnvelope, rec: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RetryableError("transient")

    await c._process_one(handler, "tp", _FakeRecord(_good_bytes()))
    assert calls == 2
    assert dlq.sent == []
    assert inner.commits == [{"tp": 8}]
    assert c.processed == 1


async def test_unexpected_exception_goes_to_dlq() -> None:
    c, inner, dlq = _make_consumer()

    async def handler(ev: EventEnvelope, rec: object) -> None:
        raise ValueError("bug on this payload shape")

    await c._process_one(handler, "tp", _FakeRecord(_good_bytes()))
    assert len(dlq.sent) == 1
    assert inner.commits == [{"tp": 8}]
    assert c.rejected == 1


@pytest.mark.parametrize("bad", [b"", b"{}", b'{"event_type":"x"}'])
async def test_various_malformed_payloads_are_quarantined(bad: bytes) -> None:
    c, _inner, dlq = _make_consumer()

    async def handler(ev: EventEnvelope, rec: object) -> None:  # pragma: no cover
        raise AssertionError

    await c._process_one(handler, "tp", _FakeRecord(bad))
    assert len(dlq.sent) == 1
