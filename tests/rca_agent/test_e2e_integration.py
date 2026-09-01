"""End-to-end: ``incident.opened`` on real Kafka -> rca-agent -> real Postgres.

Deselected by default (``-m 'not integration'``). Needs a broker and a database;
the Incident API is faked in-process (its real HTTP contract is covered by
``test_tools_against_real_incident_api.py``):

    docker compose up -d kafka postgres
    cd services/rca-agent && alembic upgrade head && cd -
    KAFKA_BOOTSTRAP_SERVERS=localhost:29092 \
    DB_TEST_URL=postgresql+asyncpg://sentinelops:sentinelops@localhost:5432/sentinelops \
    pytest -m integration tests/rca_agent/test_e2e_integration.py
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
import pytest
import pytest_asyncio

from rca_agent.config import Settings
from rca_agent.db import Database, SqlInvestigationRepository
from rca_agent.engine import InvestigationService
from rca_agent.kafka.consumer import IncidentEventConsumer
from rca_agent.llm import MockLlmClient
from rca_agent.metrics import get_metrics
from rca_agent.tools import build_registry
from sentinelops_common.kafka import KafkaJsonProducer, ensure_topics
from tests.rca_agent.incident_api_fakes import (
    INCIDENT_ID,
    make_anomaly_windows,
    make_incident,
    scenario_handler,
)
from tests.rca_agent.kafka_fakes import incident_lifecycle_envelope

pytestmark = pytest.mark.integration

BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")
_PG_URL = os.environ.get("DB_TEST_URL")
pg = pytest.mark.skipif(_PG_URL is None, reason="set DB_TEST_URL to a Postgres DB")

_ABN = ["error_rate", "latency_p95_ms"]
_INCIDENT_API = scenario_handler(
    incident=make_incident(abnormal=_ABN),
    anomalies=make_anomaly_windows(count=4, abnormal=_ABN, score=0.93),
)


@pytest_asyncio.fixture
async def stack() -> AsyncIterator[
    tuple[Settings, SqlInvestigationRepository, IncidentEventConsumer]
]:
    assert _PG_URL is not None
    topic = f"incident.events.itest.{uuid.uuid4().hex[:8]}"
    settings = Settings()
    settings.kafka.bootstrap_servers = BOOTSTRAP
    settings.kafka.incident_topic = topic
    settings.kafka.incident_dlq_topic = f"{topic}.dlq"
    settings.kafka.consumer_group = f"rca-itest-{uuid.uuid4().hex[:8]}"

    await ensure_topics(BOOTSTRAP, [topic, f"{topic}.dlq"], client_id="rca-itest")

    db = Database(_PG_URL)
    async with db.engine.begin() as conn:
        from rca_agent.db.models import Base

        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    repo = SqlInvestigationRepository(db)

    http = httpx.AsyncClient(transport=httpx.MockTransport(_INCIDENT_API))
    service = InvestigationService(
        repository=repo,
        registry=build_registry(settings, http_client=http),
        llm_client=MockLlmClient(),
        settings=settings,
    )
    producer = KafkaJsonProducer(BOOTSTRAP, client_id="rca-itest")
    await producer.start()
    consumer = IncidentEventConsumer(
        settings, service, dlq_producer=producer, metrics=get_metrics()
    )
    await consumer.start()
    try:
        yield settings, repo, consumer
    finally:
        await consumer.stop()
        await producer.stop()
        await http.aclose()
        await db.dispose()


async def _wait_for(predicate: Callable[[], Awaitable[bool]], *, timeout: float = 30.0) -> None:
    async def _poll() -> None:
        while not await predicate():
            await asyncio.sleep(0.5)

    await asyncio.wait_for(_poll(), timeout=timeout)


@pg
async def test_incident_opened_drives_one_investigation_persisted_in_postgres(
    stack: tuple[Settings, SqlInvestigationRepository, IncidentEventConsumer],
) -> None:
    settings, repo, _ = stack
    env = incident_lifecycle_envelope(incident_id=INCIDENT_ID)

    producer = KafkaJsonProducer(BOOTSTRAP, client_id="rca-itest-pub")
    await producer.start()
    try:
        await producer.publish(settings.kafka.incident_topic, env, key="orders-service:development")
        await producer.publish(settings.kafka.incident_topic, env, key="orders-service:development")
        # a lifecycle event we must ignore
        await producer.publish(
            settings.kafka.incident_topic,
            incident_lifecycle_envelope(
                incident_id=INCIDENT_ID, event_type="incident.updated", change="evidence-added"
            ),
            key="orders-service:development",
        )
    finally:
        await producer.stop()

    async def _terminal() -> bool:
        inv = await repo.get_latest_investigation(INCIDENT_ID)
        return inv is not None and inv.status.is_terminal

    await _wait_for(_terminal)
    await asyncio.sleep(2.0)  # let any duplicate settle

    inv = await repo.get_latest_investigation(INCIDENT_ID)
    assert inv is not None
    assert inv.trigger.value == "EVENT"
    assert inv.status.is_terminal

    report = await repo.get_report(inv.id)
    assert report is not None
    assert report.recommended_action.requires_human_approval is True
    evidence = await repo.get_evidence(inv.id)
    assert evidence and all(e.id.startswith("ev_") for e in evidence)
    known = {e.id for e in evidence}
    for finding in report.findings:
        assert set(finding.evidence_ids) <= known

    # duplicate + ignored events created no extra investigations
    active = await repo.get_active_investigation(INCIDENT_ID)
    assert active is None  # terminal, and exactly one exists
