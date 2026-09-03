"""Phase 6C - the anomaly-detector loading its model from the MLflow registry.

The registry-backed paths run against a temporary local sqlite MLflow store and
are skipped without ``mlflow``. The fail-safe paths (registry down) are always
exercised.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ml.data.schema import FEATURE_COLUMNS
from ml.features.engineering import build_features
from ml.inference import DetectorService
from ml.mlops.config import MLflowSettings
from ml.models import IsolationForestDetector

from anomaly_detector.app import create_app
from anomaly_detector.config import DetectorSettings, Settings
from anomaly_detector.training import (
    ensure_detector,
    ensure_detector_from_registry,
    get_detector_source,
)
from tests.ml.conftest import _make_signal_frame

_HAS_MLFLOW = importlib.util.find_spec("mlflow") is not None
_needs_mlflow = pytest.mark.skipif(not _HAS_MLFLOW, reason="mlflow not installed")


def _real_bundle(directory: Path) -> Path:
    """A genuine, fitted detector bundle that can actually score."""

    frame = _make_signal_frame(
        n=120,
        run_id="t",
        seed=7,
        anomaly_slices=[(30, 45, "latency_anomaly"), (80, 95, "error_anomaly")],
    )
    feats = build_features(frame)
    labels = feats["is_anomaly"].astype(int)
    detector = IsolationForestDetector(feature_names=list(FEATURE_COLUMNS), random_state=42)
    detector.fit(feats, labels, random_seed=42)
    detector.calibrate_threshold(feats, labels)
    path = directory / "detector.joblib"
    detector.save(path)
    return path


def _signal_record() -> dict[str, float | str]:
    from ml.data.schema import SIGNAL_COLUMNS

    row = _make_signal_frame(n=4, run_id="s", seed=1, anomaly_slices=[]).iloc[-1]
    record: dict[str, float | str] = {c: float(row[c]) for c in SIGNAL_COLUMNS}
    record["window_start"] = "2026-09-03T00:00:00Z"
    record["window_end"] = "2026-09-03T00:00:10Z"
    return record


@pytest.fixture(scope="module")
def _store(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("reg6c")


@pytest.fixture
def registry(
    _store: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> MLflowSettings:
    if not _HAS_MLFLOW:
        pytest.skip("mlflow not installed")
    monkeypatch.chdir(_store)
    name = "det-" + re.sub(r"[^A-Za-z0-9_.-]", "-", request.node.name)
    return MLflowSettings(
        tracking_uri=f"sqlite:///{(_store / 'mlflow.db').as_posix()}",
        registered_model_name=name,
        experiment_name="phase6c",
        required=False,
    )


def _register_champion(settings: MLflowSettings, bundle: Path) -> str:
    import mlflow
    from ml.mlops.registry import register_model, set_alias

    mlflow.set_tracking_uri(settings.tracking_uri)
    mlflow.set_experiment(settings.experiment_name)
    with mlflow.start_run() as run:
        mlflow.log_artifact(str(bundle), artifact_path="model")
        run_id = run.info.run_id
    version = register_model(str(bundle), run_id, settings)
    set_alias(settings, version, "champion")
    return version


# --- DetectorService.from_registry ------------------------------------------
@_needs_mlflow
def test_load_from_registry_success(registry: MLflowSettings, tmp_path: Path) -> None:
    _register_champion(registry, _real_bundle(tmp_path))

    service = DetectorService.from_registry(registry)

    assert service.source == "registry"
    assert service.model_version == "1"
    assert service.model_type == "isolation_forest"


@_needs_mlflow
def test_detector_service_from_registry_properties(
    registry: MLflowSettings, tmp_path: Path
) -> None:
    _register_champion(registry, _real_bundle(tmp_path))

    service = DetectorService.from_registry(registry)
    details = service.source_details

    assert details["alias"] == "champion"
    assert details["version"] == "1"
    assert details["model_name"] == registry.registered_model_name
    assert details["tracking_uri"] == registry.tracking_uri


@_needs_mlflow
def test_detector_service_from_registry_loads_model(
    registry: MLflowSettings, tmp_path: Path
) -> None:
    _register_champion(registry, _real_bundle(tmp_path))

    service = DetectorService.from_registry(registry)
    result = service.score_window(_signal_record())

    assert result.model_type == "isolation_forest"
    assert result.model_version == "1"
    assert isinstance(result.is_anomaly, bool)


# --- ensure_detector_from_registry fail-safe --------------------------------
@pytest.fixture
def _fast_mlflow_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """No retry storm when the registry URL is deliberately unreachable."""

    monkeypatch.setenv("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "0")
    monkeypatch.setenv("MLFLOW_HTTP_REQUEST_TIMEOUT", "1")


def _down(*, required: bool) -> MLflowSettings:
    return MLflowSettings(
        tracking_uri="http://127.0.0.1:9/none",
        registered_model_name="missing",
        required=required,
    )


def test_load_from_registry_fail_required(tmp_path: Path, _fast_mlflow_http: None) -> None:
    raised = False
    try:
        ensure_detector_from_registry(_real_bundle(tmp_path), _down(required=True), seed=42)
    except Exception:
        raised = True
    assert raised, "MLFLOW_REQUIRED=true must propagate a registry failure"


def test_load_from_registry_fail_fallback(tmp_path: Path, _fast_mlflow_http: None) -> None:
    service = ensure_detector_from_registry(_real_bundle(tmp_path), _down(required=False), seed=42)

    assert service.source == "local-fallback"
    assert get_detector_source(service)["source"] == "local-fallback"
    # the fallback model still works
    assert isinstance(service.score_window(_signal_record()).is_anomaly, bool)


def test_ensure_detector_without_mlflow_is_unchanged(tmp_path: Path) -> None:
    service = ensure_detector(_real_bundle(tmp_path), seed=42)
    assert service.source == "local"


# --- /ready + /model-info --------------------------------------------------
class _FakeProducer:
    def __init__(self, *_: object, **__: object) -> None:
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def publish(self, *_: object, **__: object) -> None:
        pass


@pytest.fixture
def _no_kafka(monkeypatch: pytest.MonkeyPatch) -> None:
    import anomaly_detector.app as appmod

    async def _noop(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(appmod, "KafkaJsonProducer", _FakeProducer)
    monkeypatch.setattr(appmod, "ensure_topics", _noop)


def _make_settings(**detector_kw: object) -> Settings:
    return Settings(
        detector=DetectorSettings(
            target_metrics_url="http://127.0.0.1:9/metrics",
            poll_interval_seconds=0.1,
            **detector_kw,  # type: ignore[arg-type]
        )
    )


def test_ready_endpoint_shows_source(tmp_path: Path, _no_kafka: None) -> None:
    settings = _make_settings(model_path=str(_real_bundle(tmp_path)), mlflow=None)
    with TestClient(create_app(settings)) as client:
        body = client.get("/ready").json()

    assert body["model_loaded"] is True
    assert body["model_source"] == "local"
    assert body["model_type"] == "isolation_forest"
    assert body["model_version"]


@_needs_mlflow
def test_ready_endpoint_shows_registry_source(
    registry: MLflowSettings, tmp_path: Path, _no_kafka: None
) -> None:
    _register_champion(registry, _real_bundle(tmp_path))
    settings = _make_settings(model_path=str(tmp_path / "unused.joblib"), mlflow=registry)

    with TestClient(create_app(settings)) as client:
        ready = client.get("/ready").json()
        info = client.get("/model-info").json()

    assert ready["model_source"] == "registry"
    assert ready["model_version"] == "1"
    assert info["source_details"]["alias"] == "champion"
