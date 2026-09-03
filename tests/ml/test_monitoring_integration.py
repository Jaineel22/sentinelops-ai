"""Phase 6D integration - baseline stored with the champion, retrieved, used.

Runs against a temporary local sqlite MLflow store; skipped without ``mlflow``.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from ml.mlops.config import MLflowSettings
from ml.mlops.promotion import _store_baseline, promote_model
from ml.mlops.registry import CHAMPION_ALIAS, get_model_baseline, resolve_alias, set_model_baseline
from ml.monitoring.baseline import freeze_baseline
from ml.monitoring.drift import detect_drift

from tests.ml.conftest import make_model_version

_HAS_MLFLOW = importlib.util.find_spec("mlflow") is not None
pytestmark = pytest.mark.skipif(not _HAS_MLFLOW, reason="mlflow not installed")

_FEATURES = ["latency_ms", "error_rate", "request_rate"]


def _frame(n: int, seed: int, latency_mean: float = 120.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "latency_ms": rng.normal(latency_mean, 20.0, n),
            "error_rate": rng.uniform(0.0, 0.05, n),
            "request_rate": rng.normal(6.0, 0.5, n),
        }
    )


@pytest.fixture
def settings(
    mlflow_sqlite: tuple[Path, str],
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> MLflowSettings:
    directory, uri = mlflow_sqlite
    monkeypatch.chdir(directory)
    name = "mon-" + re.sub(r"[^A-Za-z0-9_.-]", "-", request.node.name)
    return MLflowSettings(tracking_uri=uri, registered_model_name=name, experiment_name="p6d")


def test_baseline_retrieved_from_registry(settings: MLflowSettings) -> None:
    version, _run = make_model_version(settings)
    baseline = freeze_baseline(
        _frame(500, 1), _FEATURES, model_version=version, feature_schema_version="1"
    )

    set_model_baseline(settings, version, baseline)
    retrieved = get_model_baseline(settings, version)

    assert retrieved is not None
    assert retrieved.to_dict() == baseline.to_dict()


def test_no_baseline_returns_none(settings: MLflowSettings) -> None:
    version, _run = make_model_version(settings)
    assert get_model_baseline(settings, version) is None


def test_baseline_stored_on_promotion(settings: MLflowSettings) -> None:
    version, _run = make_model_version(settings)
    baseline = freeze_baseline(
        _frame(500, 2), _FEATURES, model_version=version, feature_schema_version="1"
    )

    promote_model(settings, version, reason="6d", baseline=baseline)

    assert resolve_alias(settings, CHAMPION_ALIAS)[0] == version
    assert get_model_baseline(settings, version) is not None


def test_store_baseline_helper_freezes_and_stores(settings: MLflowSettings) -> None:
    version, _run = make_model_version(settings)
    baseline = _store_baseline(settings, version, _frame(400, 3), _FEATURES)

    assert baseline.n_samples == 400
    assert get_model_baseline(settings, version) is not None


def test_drift_detection_end_to_end(settings: MLflowSettings) -> None:
    version, _run = make_model_version(settings)
    train = _frame(3000, seed=10)
    promote_model(
        settings,
        version,
        reason="6d e2e",
        baseline=freeze_baseline(
            train, _FEATURES, model_version=version, feature_schema_version="1"
        ),
    )

    champion_version, _run_id, _uri = resolve_alias(settings, CHAMPION_ALIAS)
    baseline = get_model_baseline(settings, champion_version)
    assert baseline is not None

    stable = detect_drift(_frame(3000, seed=11), baseline, model_version=champion_version)
    assert stable.overall_decision == "no_drift"

    drifted = detect_drift(
        _frame(3000, seed=12, latency_mean=280.0), baseline, model_version=champion_version
    )
    assert drifted.overall_decision == "significant_drift"
