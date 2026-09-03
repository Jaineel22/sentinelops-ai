"""Phase 5G: RemediationEventPublisher — topic, key, envelope, failure handling."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from remediation_controller import SERVICE_NAME
from remediation_controller.audit.builders import (
    policy_evaluated_event,
    proposal_created_event,
)
from remediation_controller.audit.model import RemediationAuditEvent
from remediation_controller.domain.enums import RemediationStatus
from remediation_controller.domain.proposal import RemediationProposal
from remediation_controller.kafka.publisher import RemediationEventPublisher
from remediation_controller.metrics import get_metrics
from tests.remediation_controller.conftest import make_proposal
from tests.remediation_controller.kafka_fakes import FakeKafkaProducer
from tests.remediation_controller.persistence_fakes import allow_decision

_NOW = datetime(2026, 9, 3, 10, 0, 0, tzinfo=UTC)
pytestmark = pytest.mark.asyncio


def _publisher(producer: FakeKafkaProducer) -> RemediationEventPublisher:
    return RemediationEventPublisher(
        producer, topic="remediation.events", source=SERVICE_NAME, metrics=get_metrics()
    )


def _events(p: RemediationProposal) -> list[RemediationAuditEvent]:
    return [
        proposal_created_event(p, correlation_id="req-9", now=_NOW),
        policy_evaluated_event(
            p, allow_decision(at=_NOW), new_state=RemediationStatus.PENDING_APPROVAL, now=_NOW
        ),
    ]


async def test_publishes_to_topic_with_remediation_id_key() -> None:
    p = make_proposal()
    producer = FakeKafkaProducer()
    await _publisher(producer).publish_audit_events(_events(p))

    assert producer.event_types() == ["remediation.proposed", "remediation.policy_evaluated"]
    assert set(producer.keys()) == {p.remediation_id}
    assert {m.topic for m in producer.messages} == {"remediation.events"}


async def test_noop_when_producer_not_ready() -> None:
    p = make_proposal()
    producer = FakeKafkaProducer(started=False)
    await _publisher(producer).publish_audit_events(_events(p))
    assert producer.messages == []


async def test_noop_when_producer_is_none() -> None:
    pub = RemediationEventPublisher(
        None, topic="remediation.events", source=SERVICE_NAME, metrics=get_metrics()
    )
    await pub.publish_audit_events(_events(make_proposal()))  # must not raise


async def test_publish_failure_is_swallowed_and_next_event_still_attempted() -> None:
    p = make_proposal()
    producer = FakeKafkaProducer(fail=True)
    # must not raise even though every publish fails
    await _publisher(producer).publish_audit_events(_events(p))
    assert producer.messages == []


async def test_execution_requested_event_is_skipped_by_publisher() -> None:
    from remediation_controller.audit.builders import execution_requested_event
    from remediation_controller.executor import new_execution_id

    p = make_proposal()
    producer = FakeKafkaProducer()
    await _publisher(producer).publish_audit_events(
        [execution_requested_event(p, execution_id=new_execution_id(), now=_NOW)]
    )
    assert producer.messages == []
