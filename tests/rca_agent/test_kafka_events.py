"""Parsing the Phase 3 ``incident.opened`` lifecycle event (Sub-phase 4E)."""

from __future__ import annotations

import pytest

from rca_agent.kafka.events import (
    IncidentOpenedEventError,
    incident_ref_from_envelope,
    is_incident_opened,
)
from tests.rca_agent.incident_api_fakes import INCIDENT_ID
from tests.rca_agent.kafka_fakes import incident_lifecycle_envelope


def test_incident_opened_is_recognised_and_parsed() -> None:
    env = incident_lifecycle_envelope(change="opened")
    assert is_incident_opened(env)
    ref = incident_ref_from_envelope(env)
    assert ref.incident_id == INCIDENT_ID
    assert ref.service == "orders-service"
    assert ref.severity == "HIGH"
    assert ref.correlation_key == "orders-service:development"


@pytest.mark.parametrize("event_type", ["incident.updated", "incident.resolved"])
def test_other_lifecycle_events_are_not_incident_opened(event_type: str) -> None:
    env = incident_lifecycle_envelope(event_type=event_type, change="evidence-added")
    assert not is_incident_opened(env)


def test_unsupported_version_is_rejected() -> None:
    env = incident_lifecycle_envelope(event_version=99)
    with pytest.raises(IncidentOpenedEventError, match="version"):
        incident_ref_from_envelope(env)


def test_malformed_payload_is_rejected() -> None:
    env = incident_lifecycle_envelope(raw_payload={"not": "an incident"})
    with pytest.raises(IncidentOpenedEventError, match="payload"):
        incident_ref_from_envelope(env)


def test_bogus_incident_id_is_rejected() -> None:
    env = incident_lifecycle_envelope(incident_id="'; DROP TABLE investigations; --")
    with pytest.raises(IncidentOpenedEventError, match="valid id"):
        incident_ref_from_envelope(env)


def test_incident_id_shape_is_enforced() -> None:
    env = incident_lifecycle_envelope(incident_id="inc_00112233")
    assert incident_ref_from_envelope(env).incident_id == "inc_00112233"
    env2 = incident_lifecycle_envelope(incident_id="incident-42")
    with pytest.raises(IncidentOpenedEventError):
        incident_ref_from_envelope(env2)
