"""anomaly.detected envelope -> AnomalySignal translation + rejection."""

from __future__ import annotations

import pytest

from incident_correlator.events import AnomalyEventError, anomaly_signal_from_envelope
from sentinelops_common.contracts import ANOMALY_DETECTED, AnomalyDetectedV1
from sentinelops_common.events import EventEnvelope


def _payload(**kw: object) -> dict[str, object]:
    base = AnomalyDetectedV1(
        detector="isolation_forest",
        detector_version="0.2.0",
        service="orders-service",
        environment="development",
        window_start="2026-09-01T12:00:00+00:00",
        window_end="2026-09-01T12:00:10+00:00",
        anomaly_score=0.91,
        threshold=0.5,
        is_anomaly=True,
        signals={"error_rate": 0.2},
        abnormal_signals=["error_rate"],
    ).model_dump()
    base.update(kw)
    return base


def _envelope(**kw: object) -> EventEnvelope:
    return EventEnvelope(
        event_type=ANOMALY_DETECTED,
        event_version=1,
        source="anomaly-detector",
        trace_id="b" * 32,
        payload=_payload(**kw),
    )


def test_valid_event_becomes_signal() -> None:
    sig = anomaly_signal_from_envelope(_envelope())
    assert sig.service == "orders-service"
    assert sig.correlation_key == "orders-service:development"
    assert sig.abnormal_signals == ["error_rate"]
    assert sig.window_start.tzinfo is not None
    assert sig.trace_id == "b" * 32


def test_wrong_event_type_rejected() -> None:
    ev = _envelope()
    ev.event_type = "order.created"
    with pytest.raises(AnomalyEventError, match="unexpected event_type"):
        anomaly_signal_from_envelope(ev)


def test_unknown_version_rejected() -> None:
    ev = _envelope()
    ev.event_version = 2
    with pytest.raises(AnomalyEventError, match="unsupported"):
        anomaly_signal_from_envelope(ev)


def test_bad_payload_rejected() -> None:
    ev = EventEnvelope(
        event_type=ANOMALY_DETECTED, event_version=1, source="x", payload={"service": "s"}
    )
    with pytest.raises(AnomalyEventError, match="does not match"):
        anomaly_signal_from_envelope(ev)


def test_non_anomaly_event_rejected() -> None:
    with pytest.raises(AnomalyEventError, match="not flagged as an anomaly"):
        anomaly_signal_from_envelope(_envelope(is_anomaly=False))
