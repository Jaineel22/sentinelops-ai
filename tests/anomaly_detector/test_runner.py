"""DetectorRunner.tick: scrape -> score -> publish, with fakes for I/O."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from ml.data.schema import SIGNAL_COLUMNS
from ml.inference import AnomalyResult

from anomaly_detector.config import Settings
from anomaly_detector.metrics import get_metrics
from anomaly_detector.metrics_source import SignalWindow
from anomaly_detector.runner import DetectorRunner
from sentinelops_common.events import EventEnvelope


class _FakeProducer:
    def __init__(self) -> None:
        self.published: list[tuple[str, EventEnvelope, str]] = []
        self.ready = True

    async def publish(self, topic: str, envelope: EventEnvelope, *, key: str, **_: object) -> None:
        self.published.append((topic, envelope, key))


class _StubDetector:
    def __init__(self, *, is_anomaly: bool) -> None:
        self._is_anomaly = is_anomaly
        self.seen: list[dict[str, object]] = []

    def score_window(self, record: dict[str, object]) -> AnomalyResult:
        self.seen.append(record)
        return AnomalyResult(
            window_start=str(record["window_start"]),
            window_end=str(record["window_end"]),
            score=0.9 if self._is_anomaly else 0.1,
            threshold=0.5,
            is_anomaly=self._is_anomaly,
            model_type="isolation_forest",
            model_version="test",
            features={},
        )


def _window(error_rate: float = 0.0) -> SignalWindow:
    signals = dict.fromkeys(SIGNAL_COLUMNS, 0.0) | {"request_rate": 4.0, "error_rate": error_rate}
    now = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    return SignalWindow(
        window_start=now, window_end=now.replace(second=10), dt_seconds=10.0, signals=signals
    )


def _runner(detector: _StubDetector, producer: _FakeProducer) -> DetectorRunner:
    settings = Settings()
    return DetectorRunner(
        settings,
        detector=detector,  # type: ignore[arg-type]
        producer=producer,  # type: ignore[arg-type]
        metrics=get_metrics(),
        client=httpx.AsyncClient(),
    )


async def test_first_scrape_yields_no_window(monkeypatch: pytest.MonkeyPatch) -> None:
    detector, producer = _StubDetector(is_anomaly=True), _FakeProducer()
    runner = _runner(detector, producer)

    async def _no_window() -> SignalWindow | None:
        return None

    monkeypatch.setattr(runner._source, "next_window", _no_window)
    await runner.tick()
    assert runner.scored == 0
    assert producer.published == []


async def test_anomalous_window_is_scored_and_published(monkeypatch: pytest.MonkeyPatch) -> None:
    detector, producer = _StubDetector(is_anomaly=True), _FakeProducer()
    runner = _runner(detector, producer)

    async def _win() -> SignalWindow | None:
        return _window(error_rate=0.4)

    monkeypatch.setattr(runner._source, "next_window", _win)
    await runner.tick()

    assert runner.scored == 1
    assert runner.published == 1
    topic, envelope, key = producer.published[0]
    assert topic == "anomaly.events"
    assert key == "orders-service"
    assert envelope.payload["is_anomaly"] is True
    assert "error_rate" in envelope.payload["abnormal_signals"]


async def test_normal_window_is_scored_but_not_published(monkeypatch: pytest.MonkeyPatch) -> None:
    detector, producer = _StubDetector(is_anomaly=False), _FakeProducer()
    runner = _runner(detector, producer)

    async def _win() -> SignalWindow | None:
        return _window()

    monkeypatch.setattr(runner._source, "next_window", _win)
    await runner.tick()

    assert runner.scored == 1
    assert producer.published == []
