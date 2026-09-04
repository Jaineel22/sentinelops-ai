"""Phase 7A - inference observability for the anomaly-detector.

The unit tests bind :class:`DetectorMetrics` to a private meter provider with an
:class:`InMemoryMetricReader`, so they read exact instrument values without
touching the process-global OTel pipeline. One end-to-end test drives a real
:class:`DetectorRunner` tick; one checks the ``GET /metrics`` exposition.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from ml.data.schema import SIGNAL_COLUMNS
from ml.inference import AnomalyResult
from opentelemetry.metrics import Counter, Histogram
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from anomaly_detector.app import create_app
from anomaly_detector.config import DetectorSettings, Settings
from anomaly_detector.metrics import DetectorMetrics
from anomaly_detector.metrics_source import SignalWindow
from anomaly_detector.runner import DetectorRunner
from sentinelops_common.events import EventEnvelope
from tests.anomaly_detector.test_registry_loading import _real_bundle


def _isolated() -> tuple[DetectorMetrics, InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    return DetectorMetrics(meter=provider.get_meter("test")), reader


def _snapshot(reader: InMemoryMetricReader) -> dict[str, list[Any]]:
    """All data points from one collection, keyed by instrument name.

    A single ``get_metrics_data()`` call per assertion block: a synchronous gauge
    only re-exports a point on the collection *after* it was ``set``, so callers
    must read every instrument they care about from the same snapshot.
    """

    data = reader.get_metrics_data()
    points: dict[str, list[Any]] = {}
    if data is None:
        return points
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                points.setdefault(metric.name, []).extend(metric.data.data_points)
    return points


def _points(reader: InMemoryMetricReader, name: str) -> list[Any]:
    return _snapshot(reader).get(name, [])


# --- instrument wiring ------------------------------------------------------
def test_metrics_initialization() -> None:
    metrics = DetectorMetrics(meter=MeterProvider().get_meter("test"))

    assert isinstance(metrics.inference_requests, Counter)
    assert isinstance(metrics.anomalies_detected, Counter)
    assert isinstance(metrics.inference_duration, Histogram)
    assert isinstance(metrics.anomaly_score, Histogram)
    assert isinstance(metrics.detection_latency, Histogram)
    # model provenance is exposed through observable gauges (Phase 7D); their
    # values come back through set_model_info + a collection (see
    # test_set_model_info_maps_version_and_type)
    # the Phase 3 scrape/publish instruments are still present
    assert isinstance(metrics.windows_scored, Counter)
    assert isinstance(metrics.anomalies_published, Counter)


def test_record_inference_counts_and_times() -> None:
    metrics, reader = _isolated()

    metrics.record_inference(model_version="7", latency_seconds=0.012, is_anomaly=False, score=0.1)
    metrics.record_inference(model_version="7", latency_seconds=0.020, is_anomaly=False, score=0.2)

    (requests,) = _points(reader, "detector.inference.requests")
    assert requests.value == 2

    (latency,) = _points(reader, "detector.inference.duration")
    assert latency.count == 2
    assert latency.sum == pytest.approx(0.032)

    (score,) = _points(reader, "detector.anomaly.score")
    assert score.count == 2


def test_record_inference_only_counts_anomalies() -> None:
    metrics, reader = _isolated()

    metrics.record_inference(model_version="7", latency_seconds=0.01, is_anomaly=False, score=0.1)
    assert _points(reader, "detector.anomalies.detected") == []

    metrics.record_inference(model_version="7", latency_seconds=0.01, is_anomaly=True, score=0.9)
    (detected,) = _points(reader, "detector.anomalies.detected")
    assert detected.value == 1


def test_metrics_labels_carry_model_version() -> None:
    metrics, reader = _isolated()

    metrics.record_inference(model_version="42", latency_seconds=0.01, is_anomaly=True, score=0.8)

    for name in (
        "detector.inference.requests",
        "detector.inference.duration",
        "detector.anomaly.score",
        "detector.anomalies.detected",
    ):
        (point,) = _points(reader, name)
        assert dict(point.attributes) == {"model_version": "42"}


def test_set_model_info_maps_version_and_type() -> None:
    metrics, reader = _isolated()

    metrics.set_model_info(version="3", model_type="isolation_forest")
    snap = _snapshot(reader)
    (version,) = snap["detector.model.version"]
    (mtype,) = snap["detector.model.type"]
    (info,) = snap["detector.model.info"]
    assert version.value == 3.0
    assert mtype.value == 1
    assert info.value == 1
    assert dict(info.attributes) == {"model_version": "3", "model_type": "isolation_forest"}

    metrics.set_model_info(version="0.2.0", model_type="random_forest")
    snap = _snapshot(reader)
    assert snap["detector.model.version"][0].value == -1.0
    assert snap["detector.model.type"][0].value == 2


def test_record_detection_latency() -> None:
    metrics, reader = _isolated()

    metrics.record_detection_latency(1.5)
    metrics.record_detection_latency(2.5)

    (latency,) = _points(reader, "detector.detection.latency")
    assert latency.count == 2
    assert latency.sum == pytest.approx(4.0)


# --- runner -> metrics wiring --------------------------------------------
class _FakeProducer:
    async def publish(self, topic: str, envelope: EventEnvelope, *, key: str, **_: object) -> None:
        return None


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


def _window() -> SignalWindow:
    signals = dict.fromkeys(SIGNAL_COLUMNS, 0.0) | {"request_rate": 4.0, "error_rate": 0.4}
    now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    return SignalWindow(
        window_start=now, window_end=now.replace(second=10), dt_seconds=10.0, signals=signals
    )


async def test_runner_tick_records_inference_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    metrics, reader = _isolated()
    runner = DetectorRunner(
        Settings(),
        detector=_StubDetector(),  # type: ignore[arg-type]
        producer=_FakeProducer(),  # type: ignore[arg-type]
        metrics=metrics,
        client=httpx.AsyncClient(),
    )

    async def _win() -> SignalWindow | None:
        return _window()

    monkeypatch.setattr(runner._source, "next_window", _win)
    await runner.tick()

    (requests,) = _points(reader, "detector.inference.requests")
    assert requests.value == 1
    assert dict(requests.attributes) == {"model_version": "9"}
    (detected,) = _points(reader, "detector.anomalies.detected")
    assert detected.value == 1
    assert _points(reader, "detector.detection.latency")


# --- /metrics exposition -------------------------------------------------
@pytest.fixture
def _no_kafka(monkeypatch: pytest.MonkeyPatch) -> None:
    import anomaly_detector.app as appmod

    class _Producer:
        def __init__(self, *_: object, **__: object) -> None: ...
        async def start(self) -> None: ...
        async def stop(self) -> None: ...
        async def publish(self, *_: object, **__: object) -> None: ...

    async def _noop(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(appmod, "KafkaJsonProducer", _Producer)
    monkeypatch.setattr(appmod, "ensure_topics", _noop)


def test_metrics_endpoint_exposes_prometheus_format(tmp_path: Path, _no_kafka: None) -> None:
    settings = Settings(
        detector=DetectorSettings(
            target_metrics_url="http://127.0.0.1:9/metrics",
            poll_interval_seconds=0.1,
            model_path=str(_real_bundle(tmp_path)),
            mlflow=None,
        )
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/metrics")

    from prometheus_client import CONTENT_TYPE_LATEST

    assert response.status_code == 200
    assert response.headers["content-type"] == CONTENT_TYPE_LATEST
    # model info is published at startup, so these gauges are always in the exposition
    assert "detector_model_version" in response.text
    assert "detector_model_type" in response.text
    assert "detector_model_info" in response.text
