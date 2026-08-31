"""End-to-end: orders-service -> Kafka -> consumer.

Deselected by default (``-m 'not integration'``). Run with a broker available:

    docker compose up -d kafka
    KAFKA_BOOTSTRAP_SERVERS=localhost:29092 pytest -m integration
"""

from __future__ import annotations

import json
import os
import uuid
from decimal import Decimal

import pytest
from aiokafka import AIOKafkaConsumer

from orders_service.config import KafkaSettings
from orders_service.domain import CreateOrderRequest, Order
from orders_service.events import build_order_created_event
from orders_service.kafka_producer import AIOKafkaEventPublisher

pytestmark = pytest.mark.integration

BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")


async def test_event_round_trips_through_kafka() -> None:
    topic = f"orders.events.itest.{uuid.uuid4().hex[:8]}"
    settings = KafkaSettings(bootstrap_servers=BOOTSTRAP, orders_topic=topic)

    publisher = AIOKafkaEventPublisher(settings)
    await publisher.start()

    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=BOOTSTRAP,
        group_id=f"itest-{uuid.uuid4().hex[:8]}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    await consumer.start()

    try:
        order = Order.create(
            CreateOrderRequest(
                customer_id="itest-customer", amount=Decimal("42.00"), currency="EUR"
            )
        )
        event = build_order_created_event(order, trace_id="a" * 32)
        await publisher.publish(
            event,
            key=order.order_id,
            headers=[("traceparent", b"00-" + b"a" * 32 + b"-" + b"b" * 16 + b"-01")],
        )

        record = await consumer.getone()
        decoded = json.loads(record.value)

        assert record.key == order.order_id.encode()
        assert decoded["event_type"] == "order.created"
        assert decoded["payload"]["order_id"] == order.order_id
        assert decoded["event_id"] == event.event_id
        assert dict(record.headers).keys() >= {"traceparent"}
    finally:
        await consumer.stop()
        await publisher.stop()
