"""Reproducible retraining workflow (Phase 6E).

Runs the real Phase 2 pipeline against the committed ``run_a`` / ``run_b`` data
and a temporary local sqlite MLflow store. Skipped without ``mlflow``.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
from ml.mlops.config import MLflowSettings
from ml.mlops.promotion import PromotionPolicy
from ml.mlops.registry import CHAMPION_ALIAS, resolve_alias
from ml.mlops.retraining import RetrainingConfig, RetrainingError, load_dataset, retrain_pipeline

from tests.ml.conftest import make_model_version

_HAS_MLFLOW = importlib.util.find_spec("mlflow") is not None
pytestmark = pytest.mark.skipif(not _HAS_MLFLOW, reason="mlflow not installed")


@pytest.fixture
def settings(
    mlflow_sqlite: tuple[Path, str],
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> MLflowSettings:
    directory, uri = mlflow_sqlite
    monkeypatch.chdir(directory)
    monkeypatch.setattr("ml.mlops.retraining.MODELS_DIR", directory / "models")
    name = "retrain-" + re.sub(r"[^A-Za-z0-9_.-]", "-", request.node.name)
    return MLflowSettings(tracking_uri=uri, registered_model_name=name, experiment_name="p6e")


@pytest.fixture
def _use_settings(settings: MLflowSettings, monkeypatch: pytest.MonkeyPatch) -> MLflowSettings:
    """`retrain_pipeline` reads config via `get_mlflow_settings()` (lru_cached) —
    point it at the per-test store."""

    monkeypatch.setattr("ml.mlops.retraining.get_mlflow_settings", lambda: settings)
    return settings


def _promote_champion(settings: MLflowSettings, *, f1: float = 0.82) -> str:
    version, _run = make_model_version(settings, f1=f1, recall=1.0, pr_auc=0.70)
    from ml.mlops.registry import set_alias

    set_alias(settings, version, CHAMPION_ALIAS)
    return version


# --- unit-ish: the reusable steps -----------------------------------------
def test_load_dataset_builds_leak_safe_split(_use_settings: MLflowSettings) -> None:
    data = load_dataset("run_a", seed=42)
    assert data.n_windows == 144
    assert len(data.feature_names) == 23
    assert len(data.x_train) > 0 and len(data.x_val) > 0 and len(data.x_test) > 0
    # chronological: train ends before val ends before test
    assert data.x_train["window_start"].max() <= data.x_val["window_start"].min()
    assert data.x_val["window_start"].max() <= data.x_test["window_start"].min()


def test_retrain_rejects_unknown_dataset(_use_settings: MLflowSettings) -> None:
    with pytest.raises(RetrainingError, match="not found"):
        retrain_pipeline(RetrainingConfig(dataset_id="does_not_exist"))


def test_retrain_rejects_unknown_model_type() -> None:
    with pytest.raises(RetrainingError, match="unknown model_type"):
        RetrainingConfig(dataset_id="run_a", model_type="deep_learning_9000")


# --- full pipeline -------------------------------------------------------
def test_retrain_success(_use_settings: MLflowSettings) -> None:
    result = retrain_pipeline(RetrainingConfig(dataset_id="run_a", seed=42))

    assert result.run_id
    assert result.candidate_version == "1"
    pointwise = result.metrics["pointwise"]
    assert 0.0 <= pointwise["f1"] <= 1.0
    assert pointwise["recall"] == pytest.approx(1.0)  # committed exp2 behaviour
    assert result.baseline_path is not None and result.baseline_path.is_file()


def test_retrain_registers_candidate(_use_settings: MLflowSettings) -> None:
    result = retrain_pipeline(RetrainingConfig(dataset_id="run_a", seed=42))
    from ml.mlops.registry import get_model_lineage, list_model_versions

    versions = list_model_versions(_use_settings)
    assert [str(v.version) for v in versions] == [result.candidate_version]
    lineage = get_model_lineage(_use_settings, result.candidate_version)
    assert lineage["params"]["workflow"] == "retrain"
    assert lineage["params"]["dataset"] == "run_a"


def test_retrain_without_champion_passes_the_gate(_use_settings: MLflowSettings) -> None:
    result = retrain_pipeline(RetrainingConfig(dataset_id="run_a", seed=42))
    assert result.champion_version is None
    assert result.promotion_decision.promote
    assert any("first model" in r for r in result.promotion_decision.reasons)


def test_retrain_evaluation_gate_compares_to_champion(_use_settings: MLflowSettings) -> None:
    champion = _promote_champion(_use_settings, f1=0.80)
    result = retrain_pipeline(RetrainingConfig(dataset_id="run_a", seed=42))

    assert result.champion_version == champion
    assert result.promotion_decision.champion_version == champion
    # run_a IF F1 ~0.82 >= champion 0.80 -> no regression -> passes
    assert result.promotion_decision.promote


def test_retrain_rejects_degraded_model(_use_settings: MLflowSettings) -> None:
    result = retrain_pipeline(
        RetrainingConfig(dataset_id="run_a", seed=42, policy=PromotionPolicy(min_f1=0.99))
    )
    assert not result.promotion_decision.promote
    assert not result.promoted
    assert any("F1" in r and "floor" in r for r in result.promotion_decision.reasons)


def test_retrain_promote_if_passing_updates_champion(_use_settings: MLflowSettings) -> None:
    result = retrain_pipeline(
        RetrainingConfig(dataset_id="run_a", seed=42, promote_if_passing=True)
    )
    assert result.promoted
    assert resolve_alias(_use_settings, CHAMPION_ALIAS)[0] == result.candidate_version


def test_retrain_promote_if_passing_false_leaves_champion(_use_settings: MLflowSettings) -> None:
    champion = _promote_champion(_use_settings, f1=0.80)
    result = retrain_pipeline(
        RetrainingConfig(dataset_id="run_a", seed=42, promote_if_passing=False)
    )
    assert result.promotion_decision.promote  # gate passed
    assert not result.promoted  # but not promoted
    assert resolve_alias(_use_settings, CHAMPION_ALIAS)[0] == champion


def test_retrain_determinism(_use_settings: MLflowSettings) -> None:
    first = retrain_pipeline(RetrainingConfig(dataset_id="run_a", seed=42))
    second = retrain_pipeline(RetrainingConfig(dataset_id="run_a", seed=42))

    assert first.metrics == second.metrics
    assert first.promotion_decision.promote == second.promotion_decision.promote
    assert first.candidate_version != second.candidate_version  # a new version each run


def test_retrain_seed_changes_are_still_reproducible(_use_settings: MLflowSettings) -> None:
    a1 = retrain_pipeline(RetrainingConfig(dataset_id="run_a", seed=7)).metrics
    a2 = retrain_pipeline(RetrainingConfig(dataset_id="run_a", seed=7)).metrics
    assert a1 == a2
