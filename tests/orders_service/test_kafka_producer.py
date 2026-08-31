"""Kafka producer wrapper behaviour (no broker required)."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from orders_service.config import KafkaSettings
from orders_service.domain import CreateOrderRequest, Order
from orders_service.events import build_order_created_event
from orders_service.kafka_producer import (
    AIOKafkaEventPublisher,
    InMemoryEventPublisher,
    PublishError,
)


def _event() -> object:
    order = Order.create(
        CreateOrderRequest(customer_id="c-1", amount=Decimal("1.00"), currency="INR")
    )
    return build_order_created_event(order, trace_id=None)


async def test_in_memory_publisher_records_calls() -> None:
    async with InMemoryEventPublisher() as publisher:
        assert publisher.ready is True
        await publisher.publish(_event(), key="ord_1", headers=[("h", b"v")])  # type: ignore[arg-type]
    assert len(publisher.published) == 1


async def test_in_memory_publisher_can_simulate_failure() -> None:
    publisher = InMemoryEventPublisher()
    await publisher.start()
    publisher.fail_next = True
    with pytest.raises(PublishError):
        await publisher.publish(_event(), key="ord_1", headers=[])  # type: ignore[arg-type]


async def test_aiokafka_publisher_raises_publish_error_when_not_started() -> None:
    publisher = AIOKafkaEventPublisher(KafkaSettings())
    assert publisher.ready is False
    with pytest.raises(PublishError):
        await publisher.publish(_event(), key="ord_1", headers=[])  # type: ignore[arg-type]


async def test_aiokafka_publisher_start_fails_fast_against_dead_broker() -> None:
    settings = KafkaSettings(
        bootstrap_servers="127.0.0.1:59999",
        auto_create_topic=False,
        request_timeout_ms=1000,
    )
    publisher = AIOKafkaEventPublisher(settings)
    with pytest.raises(Exception):  # noqa: B017 - aiokafka raises its own connection errors
        await asyncio.wait_for(publisher.start(), timeout=15)
    assert publisher.ready is False
