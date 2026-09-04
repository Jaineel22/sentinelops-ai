"""Phase 7B - detection-latency timeline.

Unit-level: the pure ``calculate_latencies`` math and the
``record_detection_timeline`` -> OTel wiring, read back through a private
:class:`InMemoryMetricReader`. Integration-level: one real ``DetectorRunner``
tick, and the timing fields riding along on the ``anomaly.detected`` payload.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from ml.data.schema import SIGNAL_COLUMNS
from ml.inference import AnomalyResult
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from anomaly_detector.config import Settings
from anomaly_detector.events import anomaly_event
from anomaly_detector.metrics import DetectorMetrics
from anomaly_detector.metrics_source import SignalWindow
from anomaly_detector.runner import DetectorRunner
from anomaly_detector.timing import (
    DetectionTimeline,
    TimingPoint,
    calculate_latencies,
    record_detection_timeline,
)
from sentinelops_common.events import EventEnvelope


def _isolated() -> tuple[DetectorMetrics, InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    return DetectorMetrics(meter=provider.get_meter("test")), reader


def _names(reader: InMemoryMetricReader) -> set[str]:
    data = reader.get_metrics_data()
    if data is None:
        return set()
    return {
        metric.name
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
    }


def _hist_point(reader: InMemoryMetricReader, name: str) -> Any:
    data = reader.get_metrics_data()
    assert data is not None
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name == name:
                    (point,) = metric.data.data_points
                    return point
    raise AssertionError(f"no metric named {name}")


def _timeline(*, is_anomaly: bool, publish: bool) -> DetectionTimeline:
    # window closed at t=1000.0, scraped 0.2s later, scored [1000.5, 1000.55],
    # published at t=1001.0  ->  end-to-end 1.0s, window age 0.2s, inference 0.05s
    return DetectionTimeline(
        scrape_time=1000.2,
        window_close_time=1000.0,
        inference_start_time=1000.5,
        inference_end_time=1000.55,
        service="orders-service",
        is_anomaly=is_anomaly,
        publish_time=1001.0 if publish else None,
    )


# --- dataclasses -------------------------------------------------------
def test_timing_point_is_frozen() -> None:
    point = TimingPoint(
        name="window_closed",
        timestamp=1000.0,
        service="orders-service",
        window_start="2026-09-01T12:00:00+00:00",
        window_end="2026-09-01T12:00:10+00:00",
    )
    assert point.name == "window_closed"
    with pytest.raises(AttributeError):
        point.timestamp = 2000.0  # type: ignore[misc]


# --- calculate_latencies -------------------------------------------------
def test_window_age_calculation() -> None:
    latencies = calculate_latencies(_timeline(is_anomaly=False, publish=False))

    assert latencies["window_age_at_scrape"] == pytest.approx(0.2)
    assert latencies["inference_duration"] == pytest.approx(0.05)
    # no publish -> no publish-dependent entries
    assert "scrape_to_publish" not in latencies
    assert "total_detection_latency" not in latencies


def test_end_to_end_latency() -> None:
    latencies = calculate_latencies(_timeline(is_anomaly=True, publish=True))

    assert latencies["scrape_to_publish"] == pytest.approx(0.8)
    assert latencies["window_to_publish"] == pytest.approx(1.0)
    assert latencies["total_detection_latency"] == pytest.approx(1.0)


def test_anomaly_without_publish_has_no_end_to_end() -> None:
    latencies = calculate_latencies(_timeline(is_anomaly=True, publish=False))

    assert "total_detection_latency" not in latencies
    assert latencies["window_age_at_scrape"] == pytest.approx(0.2)


# --- record_detection_timeline -> OTel ---------------------------------
def test_detection_timeline_recording() -> None:
    metrics, reader = _isolated()

    record_detection_timeline(_timeline(is_anomaly=True, publish=True), metrics)

    assert _hist_point(reader, "detector.window.age_at_scrape").sum == pytest.approx(0.2)
    assert _hist_point(reader, "detector.scrape.to.publish").sum == pytest.approx(0.8)
    assert _hist_point(reader, "detector.detection.latency.end_to_end").sum == pytest.approx(1.0)


def test_detection_timeline_recording_normal_window() -> None:
    metrics, reader = _isolated()

    record_detection_timeline(_timeline(is_anomaly=False, publish=False), metrics)

    names = _names(reader)
    assert "detector.window.age_at_scrape" in names
    # publish-dependent histograms stay absent until they have a measurement
    assert "detector.scrape.to.publish" not in names
    assert "detector.detection.latency.end_to_end" not in names


# --- payload -----------------------------------------------------------
def _signal_window() -> SignalWindow:
    signals = dict.fromkeys(SIGNAL_COLUMNS, 0.0) | {"request_rate": 5.0, "error_rate": 0.4}
    start = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    return SignalWindow(
        window_start=start,
        window_end=start.replace(second=10),
        dt_seconds=10.0,
        signals=signals,
        scrape_time=start.replace(second=10, microsecond=200_000),
    )


def _result(is_anomaly: bool = True) -> AnomalyResult:
    return AnomalyResult(
        window_start="2026-09-01T12:00:00+00:00",
        window_end="2026-09-01T12:00:10+00:00",
        score=0.82,
        threshold=0.5,
        is_anomaly=is_anomaly,
        model_type="isolation_forest",
        model_version="0.2.0",
        features={},
    )


def test_timeline_in_event() -> None:
    envelope = anomaly_event(
        _signal_window(),
        _result(),
        service="orders-service",
        environment="development",
        timeline=_timeline(is_anomaly=True, publish=True),
    )

    payload = envelope.payload
    assert payload["scrape_latency_ms"] == pytest.approx(200.0)
    assert payload["inference_latency_ms"] == pytest.approx(50.0)
    assert payload["detection_latency_ms"] == pytest.approx(1000.0)


def test_event_without_timeline_omits_latency_fields() -> None:
    envelope = anomaly_event(
        _signal_window(), _result(), service="orders-service", environment="development"
    )

    assert envelope.payload["detection_latency_ms"] is None
    assert envelope.payload["scrape_latency_ms"] is None
    assert envelope.payload["inference_latency_ms"] is None


# --- runner integration ----------------------------------------------
class _FakeProducer:
    def __init__(self) -> None:
        self.published: list[EventEnvelope] = []

    async def publish(self, topic: str, envelope: EventEnvelope, *, key: str, **_: object) -> None:
        self.published.append(envelope)


class _StubDetector:
    def score_window(self, record: dict[str, object]) -> AnomalyResult:
        return AnomalyResult(
            window_start=str(record["window_start"]),
            window_end=str(record["window_end"]),
            score=0.9,
            threshold=0.5,
            is_anomaly=True,
            model_type="isolation_forest",
            model_version="9",
            features={},
        )


def _live_window() -> SignalWindow:
    signals = dict.fromkeys(SIGNAL_COLUMNS, 0.0) | {"request_rate": 4.0, "error_rate": 0.4}
    now = datetime.now(tz=UTC)
    return SignalWindow(
        window_start=now.replace(microsecond=0),
        window_end=now,
        dt_seconds=10.0,
        signals=signals,
        scrape_time=now,
    )


async def test_timing_metrics_exposed(monkeypatch: pytest.MonkeyPatch) -> None:
    metrics, reader = _isolated()
    producer = _FakeProducer()
    runner = DetectorRunner(
        Settings(),
        detector=_StubDetector(),  # type: ignore[arg-type]
        producer=producer,  # type: ignore[arg-type]
        metrics=metrics,
        client=httpx.AsyncClient(),
    )

    async def _win() -> SignalWindow | None:
        return _live_window()

    monkeypatch.setattr(runner._source, "next_window", _win)
    await runner.tick()

    names = _names(reader)
    assert "detector.window.age_at_scrape" in names
    assert "detector.scrape.to.publish" in names
    assert "detector.detection.latency.end_to_end" in names
    # the published anomaly carries its latency breakdown
    (envelope,) = producer.published
    assert envelope.payload["detection_latency_ms"] is not None
    assert envelope.payload["inference_latency_ms"] is not None
    assert envelope.payload["scrape_latency_ms"] is not None
