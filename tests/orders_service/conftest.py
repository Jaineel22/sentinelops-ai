"""Fixtures for orders-service tests.

All unit tests run against an in-memory event publisher — none of them need a
running Kafka broker. The single integration test that does is marked
``@pytest.mark.integration`` and is deselected by default.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from orders_service.app import create_app
from orders_service.config import Settings
from orders_service.kafka_producer import InMemoryEventPublisher


@pytest.fixture
def settings() -> Settings:
    s = Settings()
    s.app.env = "test"
    return s


@pytest.fixture
def publisher() -> InMemoryEventPublisher:
    return InMemoryEventPublisher()


@pytest.fixture
def client(settings: Settings, publisher: InMemoryEventPublisher) -> Iterator[TestClient]:
    app = create_app(settings, publisher=publisher)
    with TestClient(app) as test_client:
        yield test_client
