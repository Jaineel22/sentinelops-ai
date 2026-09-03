"""Phase 5G integration: remediation lifecycle -> real Kafka.

Deselected by default (``-m 'not integration'``). Needs a broker (and, for the
PostgreSQL-authoritative check, ``DB_TEST_URL``):

    docker compose up -d kafka postgres
    (cd services/remediation-controller && alembic upgrade head)
    KAFKA_BOOTSTRAP_SERVERS=localhost:29092 \
    DB_TEST_URL=postgresql+asyncpg://sentinelops:sentinelops@localhost:5432/sentinelops \
    pytest -m integration tests/remediation_controller/test_integration_kafka.py
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from aiokafka import AIOKafkaConsumer

from remediation_controller import SERVICE_NAME
from remediation_controller.domain import ApprovalDecision, ApproverRole, RemediationStatus
from remediation_controller.domain.proposal import RcaRecommendedActionInput
from remediation_controller.kafka.publisher import RemediationEventPublisher
from remediation_controller.metrics import get_metrics
from remediation_controller.recovery.config import RecoveryVerificationConfig
from remediation_controller.repository import InMemoryRemediationRepository
from remediation_controller.service import RemediationService
from sentinelops_common.contracts import (
    REMEDIATION_LIFECYCLE_EVENT_TYPES,
    RemediationLifecycleV1,
)
from sentinelops_common.kafka import KafkaJsonProducer, ensure_topics
from tests.remediation_controller.persistence_fakes import BASE_TIME

pytestmark = pytest.mark.integration

BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")
_INCIDENT = "inc_00112233aabbccdd"
_FAST = RecoveryVerificationConfig(timeout_seconds=6, poll_interval_seconds=1.0)


@pytest_asyncio.fixture
async def kafka_topic() -> AsyncIterator[str]:
    topic = f"remediation.events.itest.{uuid.uuid4().hex[:8]}"
    await ensure_topics(BOOTSTRAP, [topic], client_id="rem-itest")
    yield topic


async def _drive_full_lifecycle(svc: RemediationService) -> str:
    rec = await svc.propose(
        incident_id=_INCIDENT,
        recommendation=RcaRecommendedActionInput(
            action_type="RESTART_SERVICE", target_service="orders-service"
        ),
        incident_severity="HIGH",
        now=BASE_TIME,
        correlation_id="itest-req",
    )
    await svc.decide(
        rec.remediation_id,
        decision=ApprovalDecision.APPROVE,
        approver_identity="alice@example.com",
        approver_role=ApproverRole.ADMINISTRATOR,
        now=BASE_TIME,
    )
    await svc.execute(rec.remediation_id, now=BASE_TIME)
    out = await svc.verify_recovery(rec.remediation_id, now=BASE_TIME)
    assert out.record.status is RemediationStatus.RECOVERED
    return rec.remediation_id


async def test_lifecycle_events_round_trip_ordered_by_remediation_id(kafka_topic: str) -> None:
    producer = KafkaJsonProducer(BOOTSTRAP, client_id="rem-itest-prod")
    await producer.start()
    consumer = AIOKafkaConsumer(
        kafka_topic,
        bootstrap_servers=BOOTSTRAP,
        group_id=f"itest-{uuid.uuid4().hex[:8]}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    await consumer.start()
    try:
        publisher = RemediationEventPublisher(
            producer, topic=kafka_topic, source=SERVICE_NAME, metrics=get_metrics()
        )
        svc = RemediationService(
            repository=InMemoryRemediationRepository(),
            verify_config=_FAST,
            event_publisher=publisher,
        )
        rid = await _drive_full_lifecycle(svc)

        seen: list[tuple[str, bytes]] = []
        for _ in range(7):
            record = await consumer.getone()
            seen.append((record.value and json.loads(record.value)["event_type"], record.key))

        types = [t for t, _ in seen]
        assert types == [
            "remediation.proposed",
            "remediation.policy_evaluated",
            "remediation.approved",
            "remediation.execution_started",
            "remediation.execution_succeeded",
            "remediation.recovery_verification_started",
            "remediation.recovered",
        ]
        # one partition, one key -> ordering is guaranteed
        assert {k for _, k in seen} == {rid.encode()}
        for t in types:
            assert t in REMEDIATION_LIFECYCLE_EVENT_TYPES
    finally:
        await consumer.stop()
        await producer.stop()


@pytest.mark.skipif(
    os.environ.get("DB_TEST_URL") is None,
    reason="set DB_TEST_URL for the authoritative-state check",
)
async def test_postgres_state_remains_authoritative_when_kafka_unavailable() -> None:
    """Kafka being unreachable must not corrupt the DB or the audit trail — the
    lifecycle still completes; the events are simply not published."""

    from remediation_controller.db import Database, SqlRemediationRepository

    url = os.environ["DB_TEST_URL"]
    db = Database(url)
    async with db.engine.begin() as conn:
        from remediation_controller.db.models import Base

        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    try:
        # a producer that was never started -> publisher is a safe no-op.
        dead = KafkaJsonProducer("localhost:65533", client_id="rem-itest-dead")
        publisher = RemediationEventPublisher(
            dead, topic="remediation.events.unused", source=SERVICE_NAME, metrics=get_metrics()
        )
        svc = RemediationService(
            repository=SqlRemediationRepository(db),
            verify_config=_FAST,
            event_publisher=publisher,
        )
        rid = await _drive_full_lifecycle(svc)

        final = await svc.get(rid)
        assert final is not None and final.status is RemediationStatus.RECOVERED
        events = await svc.list_audit_events(rid)
        assert events is not None
        assert [e.event_type for e in events][-1].value == "VERIFICATION_SUCCEEDED"
    finally:
        await db.dispose()


def test_contract_payload_carries_only_safe_fields() -> None:
    banned = {"command", "script", "shell", "cmd", "url", "endpoint", "token", "password", "secret"}
    assert set(RemediationLifecycleV1.model_fields).isdisjoint(banned)
