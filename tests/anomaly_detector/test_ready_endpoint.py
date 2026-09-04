"""Phase 7C - the enhanced ``/ready`` (and ``/ready/stats``) endpoints.

The existing ``/ready`` contract (status / model_* fields, 200 vs 503) must not
regress; the new fields sit alongside it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from anomaly_detector.app import create_app
from anomaly_detector.config import DetectorSettings, HealthSettings, Settings
from tests.anomaly_detector.test_registry_loading import _real_bundle


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


def _settings(tmp_path: Path, health: HealthSettings | None = None) -> Settings:
    return Settings(
        detector=DetectorSettings(
            target_metrics_url="http://127.0.0.1:9/metrics",
            poll_interval_seconds=0.05,
            model_path=str(_real_bundle(tmp_path)),
            mlflow=None,
        ),
        health=health or HealthSettings(),
    )


def test_ready_endpoint_basic(tmp_path: Path, _no_kafka: None) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    # existing contract
    assert body["status"] == "ready"
    assert body["model_loaded"] is True
    assert body["model_source"] == "local"
    assert body["model_type"] == "isolation_forest"
    assert body["model_version"]


def test_ready_endpoint_with_stats(tmp_path: Path, _no_kafka: None) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        body = client.get("/ready").json()

    assert "inference_stats" in body
    stats = body["inference_stats"]
    assert stats["total_inferences"] == 0
    assert stats["total_anomalies"] == 0
    assert stats["anomaly_rate"] == 0.0
    assert stats["last_inference_time"] is None
    assert isinstance(body["uptime_seconds"], (int, float))
    assert body["healthy"] is True
    assert body["health_reasons"] == []


def test_ready_endpoint_after_inferences(tmp_path: Path, _no_kafka: None) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        state = client.app.state.detector_state  # type: ignore[attr-defined]
        state.record_inference(latency_seconds=0.010, is_anomaly=False)
        state.record_inference(latency_seconds=0.020, is_anomaly=True)

        stats = client.get("/ready").json()["inference_stats"]

    assert stats["total_inferences"] == 2
    assert stats["total_anomalies"] == 1
    assert stats["anomaly_rate"] == pytest.approx(50.0)
    assert stats["last_latency_ms"] == pytest.approx(20.0)
    assert stats["min_latency_ms"] == pytest.approx(10.0)
    assert stats["max_latency_ms"] == pytest.approx(20.0)
    assert stats["last_inference_time"] is not None


def test_ready_endpoint_unhealthy(tmp_path: Path, _no_kafka: None) -> None:
    health = HealthSettings(
        unhealthy_after_no_inference_seconds=1.0,
        unhealthy_if_anomaly_rate_above=10.0,
        unhealthy_if_avg_latency_above_ms=1.0,
    )
    with TestClient(create_app(_settings(tmp_path, health))) as client:
        state = client.app.state.detector_state  # type: ignore[attr-defined]
        state.record_inference(latency_seconds=0.5, is_anomaly=True)  # 500ms, anomaly
        # make the last inference look stale
        state.last_inference_time = datetime.now(tz=UTC) - timedelta(seconds=30)

        body = client.get("/ready").json()

    # scoring loop is alive -> still 200 / "ready", but flagged degraded
    assert body["status"] == "ready"
    assert body["healthy"] is False
    assert len(body["health_reasons"]) == 3


def test_ready_stats_endpoint(tmp_path: Path, _no_kafka: None) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        client.app.state.detector_state.record_inference(  # type: ignore[attr-defined]
            latency_seconds=0.01, is_anomaly=False
        )
        response = client.get("/ready/stats")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"inference_stats", "uptime_seconds", "healthy", "health_reasons"}
    assert body["inference_stats"]["total_inferences"] == 1
    # the stats view omits the model_* / status fields
    assert "model_version" not in body
