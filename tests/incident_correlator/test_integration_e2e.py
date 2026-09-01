"""End-to-end: anomaly.detected on Kafka -> incident-correlator -> Postgres.

Deselected by default (``-m 'not integration'``). Needs a broker and a database:

    docker compose up -d kafka postgres
    cd services/incident-correlator && alembic upgrade head && cd -
    KAFKA_BOOTSTRAP_SERVERS=localhost:29092 \
    DB_TEST_URL=postgresql+asyncpg://sentinelops:sentinelops@localhost:5432/sentinelops \
    pytest -m integration tests/incident_correlator/test_integration_e2e.py
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
import pytest_asyncio

from incident_correlator.config import Settings
from incident_correlator.consumer import AnomalyConsumer
from incident_correlator.db import Database, SqlIncidentRepository
from incident_correlator.metrics import CorrelatorMetrics
from incident_correlator.processor import AnomalyProcessor
from incident_correlator.repository import IncidentFilter
from sentinelops_common.contracts import (
    ANOMALY_DETECTED,
    ANOMALY_DETECTED_VERSION,
    AnomalyDetectedV1,
)
from sentinelops_common.events import EventEnvelope
from sentinelops_common.kafka import KafkaJsonProducer, ensure_topics

pytestmark = pytest.mark.integration

BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")
_PG_URL = os.environ.get("DB_TEST_URL")
pg = pytest.mark.skipif(_PG_URL is None, reason="set DB_TEST_URL to a Postgres DB")


def _anomaly(service: str, *, offset: int, event_id: str | None = None) -> EventEnvelope:
    payload = AnomalyDetectedV1(
        detector="isolation_forest",
        detector_version="test",
        service=service,
        environment="development",
        window_start=f"2026-09-01T12:{offset:02d}:00+00:00",
        window_end=f"2026-09-01T12:{offset:02d}:10+00:00",
        anomaly_score=0.9,
        threshold=0.5,
        is_anomaly=True,
        signals={"error_rate": 0.4, "latency_p95_ms": 800.0},
        abnormal_signals=["error_rate", "latency_p95_ms"],
    )
    return EventEnvelope(
        event_id=event_id or str(uuid.uuid4()),
        event_type=ANOMALY_DETECTED,
        event_version=ANOMALY_DETECTED_VERSION,
        source="anomaly-detector",
        payload=payload.model_dump(),
    )


@pytest_asyncio.fixture
async def stack() -> AsyncIterator[tuple[Settings, SqlIncidentRepository, AnomalyConsumer]]:
    assert _PG_URL is not None
    topic = f"anomaly.events.itest.{uuid.uuid4().hex[:8]}"
    settings = Settings()
    settings.kafka.bootstrap_servers = BOOTSTRAP
    settings.kafka.anomaly_topic = topic
    settings.kafka.anomaly_dlq_topic = f"{topic}.dlq"
    settings.kafka.consumer_group = f"itest-{uuid.uuid4().hex[:8]}"
    settings.correlation.window_seconds = 300.0

    await ensure_topics(BOOTSTRAP, [topic, f"{topic}.dlq"], client_id="itest", num_partitions=1)

    db = Database(_PG_URL)
    async with db.engine.begin() as conn:
        from incident_correlator.db.models import Base

        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    repo = SqlIncidentRepository(db)

    producer = KafkaJsonProducer(BOOTSTRAP, client_id="itest")
    await producer.start()
    processor = AnomalyProcessor(repo, correlation_config=settings.correlation)
    consumer = AnomalyConsumer(
        settings, processor, dlq_producer=producer, metrics=CorrelatorMetrics()
    )
    await consumer.start()
    try:
        yield settings, repo, consumer
    finally:
        await consumer.stop()
        await producer.stop()
        await db.dispose()


async def _wait_for(predicate: Callable[[], Awaitable[bool]], *, timeout: float = 20.0) -> None:
    async def _poll() -> None:
        while not await predicate():
            await asyncio.sleep(0.5)

    await asyncio.wait_for(_poll(), timeout=timeout)


@pg
async def test_related_anomalies_become_one_incident_and_duplicates_are_ignored(
    stack: tuple[Settings, SqlIncidentRepository, AnomalyConsumer],
) -> None:
    settings, repo, _ = stack
    producer = KafkaJsonProducer(BOOTSTRAP, client_id="itest-pub")
    await producer.start()
    try:
        dup = _anomaly("orders-service", offset=0, event_id="dup-1")
        for env in (
            dup,
            _anomaly("orders-service", offset=1),
            _anomaly("payments-service", offset=2),
            dup,  # replay — must be idempotent
        ):
            await producer.publish(settings.kafka.anomaly_topic, env, key=env.payload["service"])
    finally:
        await producer.stop()

    async def _two_incidents() -> bool:
        return len(await repo.list_incidents(IncidentFilter())) == 2

    await _wait_for(_two_incidents)
    # let any duplicate settle
    await asyncio.sleep(2.0)

    incidents = {i.service: i for i in await repo.list_incidents(IncidentFilter())}
    assert set(incidents) == {"orders-service", "payments-service"}
    assert incidents["orders-service"].anomaly_count == 2
    assert incidents["payments-service"].anomaly_count == 1

    evidence = await repo.get_evidence(incidents["orders-service"].id)
    assert evidence is not None and len(evidence) == 2
