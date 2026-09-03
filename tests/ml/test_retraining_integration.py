"""Phase 6E integration - the full retraining flow, and drift -> retrain.

Uses a temporary local sqlite MLflow store (a real MLflow backend). Skipped
without ``mlflow``; also tagged ``mlflow`` so ``pytest -m mlflow`` selects them.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
from ml.data.prepare import load_processed_run
from ml.data.schema import FEATURE_COLUMNS
from ml.features.engineering import build_features
from ml.mlops.config import MLflowSettings
from ml.mlops.registry import CHAMPION_ALIAS, get_model_baseline, resolve_alias
from ml.mlops.retraining import RetrainingConfig, retrain_pipeline
from ml.monitoring.drift import detect_drift

_HAS_MLFLOW = importlib.util.find_spec("mlflow") is not None
pytestmark = [
    pytest.mark.skipif(not _HAS_MLFLOW, reason="mlflow not installed"),
    pytest.mark.mlflow,
]


@pytest.fixture
def store(
    mlflow_sqlite: tuple[Path, str],
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> MLflowSettings:
    directory, uri = mlflow_sqlite
    monkeypatch.chdir(directory)
    monkeypatch.setattr("ml.mlops.retraining.MODELS_DIR", directory / "models")
    name = "e2e-" + re.sub(r"[^A-Za-z0-9_.-]", "-", request.node.name)
    settings = MLflowSettings(
        tracking_uri=uri, registered_model_name=name, experiment_name="p6e-e2e"
    )
    monkeypatch.setattr("ml.mlops.retraining.get_mlflow_settings", lambda: settings)
    return settings


def test_retraining_flow_with_real_mlflow(store: MLflowSettings) -> None:
    # train -> track -> register -> gate -> promote, all through one call
    result = retrain_pipeline(
        RetrainingConfig(dataset_id="run_a", seed=42, promote_if_passing=True)
    )

    assert result.promoted
    version, run_id, uri = resolve_alias(store, CHAMPION_ALIAS)
    assert version == result.candidate_version == "1"
    assert run_id == result.run_id
    assert uri.endswith("@champion")
    # the champion carries a drift baseline (6D <-> 6E)
    assert get_model_baseline(store, version) is not None


def test_drift_triggers_retraining(store: MLflowSettings) -> None:
    # 1. a champion + its baseline
    champion = retrain_pipeline(
        RetrainingConfig(dataset_id="run_a", seed=42, promote_if_passing=True)
    )
    baseline = get_model_baseline(store, champion.candidate_version)
    assert baseline is not None

    # 2. drift check against "current" data (run_b: publish-failure/surge instead
    #    of latency/error) -> significant
    current = build_features(load_processed_run("run_b"))[list(FEATURE_COLUMNS)]
    report = detect_drift(current, baseline, model_version=champion.candidate_version)
    assert report.overall_decision == "significant_drift"

    # 3. drift -> retrain on run_b -> gate runs vs the champion
    retrained = retrain_pipeline(RetrainingConfig(dataset_id="run_b", seed=42))
    assert retrained.candidate_version == "2"
    assert retrained.champion_version == champion.candidate_version
    assert isinstance(retrained.promotion_decision.promote, bool)
    # nothing auto-promoted (promote_if_passing defaulted False)
    assert not retrained.promoted
    assert resolve_alias(store, CHAMPION_ALIAS)[0] == champion.candidate_version
