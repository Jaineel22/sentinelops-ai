"""The outbound anomaly.detected envelope is consumable by the incident-correlator.

This is the Phase 2 -> Phase 3 contract check: what anomaly-detector produces,
incident-correlator must be able to normalise into an AnomalySignal.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ml.data.schema import SIGNAL_COLUMNS
from ml.inference import AnomalyResult

from anomaly_detector.events import anomaly_event
from anomaly_detector.metrics_source import SignalWindow
from incident_correlator.events import anomaly_signal_from_envelope

_SIGNALS = dict.fromkeys(SIGNAL_COLUMNS, 0.0) | {
    "request_rate": 5.0,
    "error_rate": 0.35,
    "latency_p95_ms": 780.0,
}


def _window() -> SignalWindow:
    start = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    end = datetime(2026, 9, 1, 12, 0, 10, tzinfo=UTC)
    return SignalWindow(window_start=start, window_end=end, dt_seconds=10.0, signals=dict(_SIGNALS))


def _result(is_anomaly: bool = True) -> AnomalyResult:
    return AnomalyResult(
        window_start="2026-09-01T12:00:00+00:00",
        window_end="2026-09-01T12:00:10+00:00",
        score=0.82,
        threshold=0.49,
        is_anomaly=is_anomaly,
        model_type="isolation_forest",
        model_version="0.2.0",
        features={"error_rate": 0.35},
    )


def test_envelope_roundtrips_into_an_anomaly_signal() -> None:
    envelope = anomaly_event(
        _window(), _result(), service="orders-service", environment="development"
    )
    assert envelope.event_type == "anomaly.detected"
    assert envelope.event_version == 1

    signal = anomaly_signal_from_envelope(envelope)
    assert signal.service == "orders-service"
    assert signal.environment == "development"
    assert signal.correlation_key == "orders-service:development"
    assert signal.anomaly_score == 0.82
    assert "error_rate" in signal.abnormal_signals
    assert "latency_p95_ms" in signal.abnormal_signals


def test_non_anomalous_window_is_rejected_by_the_correlator() -> None:
    import pytest

    from incident_correlator.events import AnomalyEventError

    envelope = anomaly_event(
        _window(), _result(is_anomaly=False), service="orders-service", environment="development"
    )
    with pytest.raises(AnomalyEventError):
        anomaly_signal_from_envelope(envelope)
