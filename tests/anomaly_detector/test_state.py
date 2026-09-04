"""Phase 7C - the in-process inference-statistics rollup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from ml.data.schema import SIGNAL_COLUMNS
from ml.inference import AnomalyResult

from anomaly_detector.config import Settings
from anomaly_detector.metrics import get_metrics
from anomaly_detector.metrics_source import SignalWindow
from anomaly_detector.runner import DetectorRunner
from anomaly_detector.state import DetectorState, assess_health

_SUMMARY_KEYS = {
    "total_inferences",
    "total_anomalies",
    "anomaly_rate",
    "avg_latency_ms",
    "last_latency_ms",
    "min_latency_ms",
    "max_latency_ms",
    "last_inference_time",
}


def test_state_initialization() -> None:
    state = DetectorState()

    assert state.inference_count == 0
    assert state.anomaly_count == 0
    assert state.last_inference_time is None
    assert isinstance(state.startup_time, datetime)
    assert state.startup_time.tzinfo is UTC
    assert state.uptime_seconds() >= 0.0
    assert state.seconds_since_last_inference() is None

    summary = state.get_summary()
    assert summary["total_inferences"] == 0
    assert summary["anomaly_rate"] == 0.0  # no division by zero
    assert summary["last_inference_time"] is None


def test_record_inference_updates_stats() -> None:
    state = DetectorState()

    state.record_inference(latency_seconds=0.010, is_anomaly=False)
    state.record_inference(latency_seconds=0.030, is_anomaly=False)

    assert state.inference_count == 2
    assert state.anomaly_count == 0
    assert state.last_inference_latency_ms == pytest.approx(30.0)
    assert state.min_inference_latency_ms == pytest.approx(10.0)
    assert state.max_inference_latency_ms == pytest.approx(30.0)
    assert isinstance(state.last_inference_time, datetime)
    assert state.seconds_since_last_inference() is not None


def test_record_anomaly_updates_anomaly_count() -> None:
    state = DetectorState()

    state.record_inference(latency_seconds=0.01, is_anomaly=True)
    state.record_inference(latency_seconds=0.01, is_anomaly=False)
    state.record_inference(latency_seconds=0.01, is_anomaly=True)

    assert state.inference_count == 3
    assert state.anomaly_count == 2
    assert state.get_summary()["anomaly_rate"] == pytest.approx(66.67)


def test_avg_latency_calculation() -> None:
    state = DetectorState()

    # first sample seeds the average exactly
    state.record_inference(latency_seconds=0.010, is_anomaly=False)
    assert state.avg_inference_latency_ms == pytest.approx(10.0)

    # subsequent samples fold in with EMA weight 0.2
    state.record_inference(latency_seconds=0.020, is_anomaly=False)
    assert state.avg_inference_latency_ms == pytest.approx(0.2 * 20.0 + 0.8 * 10.0)  # 12.0

    # the average stays between the min and max seen
    for _ in range(50):
        state.record_inference(latency_seconds=0.050, is_anomaly=False)
    assert 10.0 <= state.avg_inference_latency_ms <= 50.0
    assert state.max_inference_latency_ms == pytest.approx(50.0)
    assert state.min_inference_latency_ms == pytest.approx(10.0)


def test_get_summary_returns_all_fields() -> None:
    state = DetectorState()
    state.record_inference(latency_seconds=0.012, is_anomaly=True)

    summary = state.get_summary()
    assert set(summary) == _SUMMARY_KEYS
    assert summary["total_inferences"] == 1
    assert summary["total_anomalies"] == 1
    assert summary["last_inference_time"] is not None
    # every value must be JSON-serialisable (str / int / float / None)
    for value in summary.values():
        assert value is None or isinstance(value, (str, int, float))


def test_reset_zeroes_counters_but_keeps_startup() -> None:
    state = DetectorState()
    started = state.startup_time
    state.record_inference(latency_seconds=0.01, is_anomaly=True)

    state.reset()

    assert state.inference_count == 0
    assert state.anomaly_count == 0
    assert state.last_inference_time is None
    assert state.startup_time == started


# --- assess_health ----------------------------------------------------
def test_assess_health_ok_for_fresh_state() -> None:
    state = DetectorState()
    healthy, reasons = assess_health(
        state.get_summary(),
        uptime_seconds=5.0,
        seconds_since_last_inference=None,
        max_idle_seconds=300.0,
        max_anomaly_rate=50.0,
        max_avg_latency_ms=5000.0,
    )
    assert healthy is True
    assert reasons == []


def test_assess_health_flags_each_threshold() -> None:
    summary = {"total_inferences": 100, "anomaly_rate": 80.0, "avg_latency_ms": 9000.0}

    healthy, reasons = assess_health(
        summary,
        uptime_seconds=1000.0,
        seconds_since_last_inference=600.0,
        max_idle_seconds=300.0,
        max_anomaly_rate=50.0,
        max_avg_latency_ms=5000.0,
    )
    assert healthy is False
    assert len(reasons) == 3


def test_assess_health_flags_idle_since_startup() -> None:
    healthy, reasons = assess_health(
        {"total_inferences": 0, "anomaly_rate": 0.0, "avg_latency_ms": 0.0},
        uptime_seconds=400.0,
        seconds_since_last_inference=None,
        max_idle_seconds=300.0,
        max_anomaly_rate=50.0,
        max_avg_latency_ms=5000.0,
    )
    assert healthy is False
    assert "startup" in reasons[0]


# --- runner -> state wiring -----------------------------------------
class _FakeProducer:
    async def publish(self, *_: object, **__: object) -> None:
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
    now = datetime.now(tz=UTC)
    return SignalWindow(
        window_start=now - timedelta(seconds=10),
        window_end=now,
        dt_seconds=10.0,
        signals=signals,
        scrape_time=now,
    )


async def test_runner_tick_updates_state(monkeypatch: pytest.MonkeyPatch) -> None:
    state = DetectorState()
    runner = DetectorRunner(
        Settings(),
        detector=_StubDetector(),  # type: ignore[arg-type]
        producer=_FakeProducer(),  # type: ignore[arg-type]
        metrics=get_metrics(),
        client=httpx.AsyncClient(),
        state=state,
    )

    async def _win() -> SignalWindow | None:
        return _window()

    monkeypatch.setattr(runner._source, "next_window", _win)
    await runner.tick()

    assert state.inference_count == 1
    assert state.anomaly_count == 1
    assert state.last_inference_time is not None
