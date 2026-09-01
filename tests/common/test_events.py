"""The shared event envelope + decoder."""

from __future__ import annotations

import json

import pytest

from sentinelops_common.events import EventDecodeError, EventEnvelope, parse_envelope


def _envelope(**overrides: object) -> EventEnvelope:
    base: dict[str, object] = {
        "event_type": "anomaly.detected",
        "event_version": 1,
        "source": "anomaly-detector",
        "payload": {"service": "orders-service"},
    }
    base.update(overrides)
    return EventEnvelope(**base)  # type: ignore[arg-type]


def test_envelope_defaults_and_roundtrip() -> None:
    ev = _envelope()
    assert ev.event_id  # auto uuid
    assert ev.occurred_at.tzinfo is not None
    decoded = parse_envelope(ev.to_json_bytes())
    assert decoded.event_id == ev.event_id
    assert decoded.event_type == "anomaly.detected"
    assert decoded.payload == {"service": "orders-service"}


def test_kafka_headers() -> None:
    ev = _envelope(event_id="abc", event_version=2)
    headers = dict(ev.kafka_headers())
    assert headers["event-type"] == b"anomaly.detected"
    assert headers["event-id"] == b"abc"
    assert headers["event-version"] == b"2"


def test_parse_rejects_non_json() -> None:
    with pytest.raises(EventDecodeError, match="not valid JSON"):
        parse_envelope(b"\x00not json")


def test_parse_rejects_missing_required_field() -> None:
    bad = json.dumps({"event_type": "x", "event_version": 1, "source": "s"})  # no payload
    with pytest.raises(EventDecodeError, match="does not match the event envelope"):
        parse_envelope(bad)


def test_parse_rejects_wrong_type() -> None:
    bad = json.dumps({"event_type": "x", "event_version": "one", "source": "s", "payload": {}})
    with pytest.raises(EventDecodeError):
        parse_envelope(bad)
