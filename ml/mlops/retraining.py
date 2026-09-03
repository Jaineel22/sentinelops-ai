"""Reproducible retraining workflow (Phase 6E).

`retrain_pipeline(config)` runs the **existing** Phase 2 pipeline end to end and
threads the result through the Phase 6 lifecycle:

    load dataset (ml.data.prepare)         -> chronological split (ml.splits)
      -> features (ml.features.engineering) -> train (ml.models)
      -> calibrate threshold on validation  -> evaluate on test (ml.evaluation)
      -> freeze drift baseline (ml.monitoring, 6D)
      -> log run + baseline to MLflow (6A)  -> register as a new version (6B)
      -> compare candidate vs champion with the deterministic gate (6B)
      -> promote only if `promote_if_passing` and the gate passes

Deterministic (same `dataset_id` + `seed` -> same metrics), no LLM, no autonomous
deployment - promotion is opt-in and goes through the same `evaluate_candidate`
gate the CLI uses.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ml.config import MODELS_DIR, set_global_seed
from ml.data.prepare import load_processed_run
from ml.data.schema import FEATURE_COLUMNS, FEATURE_SCHEMA_VERSION
from ml.evaluation.metrics import evaluate
from ml.features.engineering import build_features
from ml.mlops.config import ensure_local_tracking_store, get_mlflow_settings
from ml.mlops.promotion import (
    PromotionDecision,
    PromotionPolicy,
    evaluate_candidate,
    promote_model,
)
from ml.mlops.registry import (
    CHAMPION_ALIAS,
    get_champion_metrics,
    register_model,
    resolve_alias,
    set_model_baseline,
)
from ml.mlops.tracking import _numeric_metrics, log_run
from ml.models import IsolationForestDetector, RandomForestDetector, RobustZScoreDetector
from ml.models.base import AnomalyDetector
from ml.monitoring.baseline import freeze_baseline, save_baseline
from ml.splits import chronological_split

logger = logging.getLogger("ml.mlops.retraining")

# Same chronological split as ml.experiments.catalog / anomaly_detector.training.
_VAL_FRACTION = 0.17
_TEST_FRACTION = 0.33
_THRESHOLD_OBJECTIVE = "f1"

_MODEL_FACTORIES: dict[str, Callable[[list[str], int], AnomalyDetector]] = {
    "isolation_forest": lambda names, seed: IsolationForestDetector(
        feature_names=names, random_state=seed, n_estimators=200
    ),
    "robust_zscore": lambda names, _seed: RobustZScoreDetector(feature_names=names),
    "random_forest_supervised": lambda names, seed: RandomForestDetector(
        feature_names=names, random_state=seed
    ),
}
SUPPORTED_MODEL_TYPES: tuple[str, ...] = tuple(_MODEL_FACTORIES)


class RetrainingError(RuntimeError):
    """Retraining could not complete (bad config, dataset, or MLflow unavailable)."""


def _noop(_message: str) -> None:
    return None


@dataclass(frozen=True)
class RetrainingConfig:
    dataset_id: str
    seed: int = 42
    model_type: str = "isolation_forest"
    promote_if_passing: bool = False
    policy: PromotionPolicy | None = None

    def __post_init__(self) -> None:
        if self.model_type not in _MODEL_FACTORIES:
            raise RetrainingError(
                f"unknown model_type {self.model_type!r}; supported: {SUPPORTED_MODEL_TYPES}"
            )


@dataclass
class DatasetSplits:
    x_train: pd.DataFrame
    y_train: pd.Series
    x_val: pd.DataFrame
    y_val: pd.Series
    x_test: pd.DataFrame
    y_test: pd.Series
    feature_names: list[str]
    window_seconds: float
    n_windows: int


@dataclass
class RetrainingResult:
    candidate_version: str
    promotion_decision: PromotionDecision
    metrics: dict[str, Any]
    run_id: str
    champion_version: str | None
    promoted: bool
    baseline_path: Path | None


def _ensure_consistent_features(x: pd.DataFrame, feature_names: list[str]) -> None:
    """The feature frame must contain every training feature, by name."""

    missing = [c for c in feature_names if c not in x.columns]
    if missing:
        raise RetrainingError(f"feature frame is missing columns: {missing}")


def _get_save_paths(temp_dir: str | Path, model_type: str) -> tuple[Path, Path]:
    base = Path(temp_dir)
    return base / f"{model_type}.joblib", base / f"{model_type}__baseline.joblib"


def load_dataset(dataset_id: str, seed: int) -> DatasetSplits:
    """Load a processed run and build the same leak-safe chronological split the
    Phase 2 experiments use."""

    set_global_seed(seed)
    try:
        raw = load_processed_run(dataset_id)
    except FileNotFoundError as exc:
        raise RetrainingError(f"dataset {dataset_id!r} not found: {exc}") from exc

    feats = build_features(raw)
    _ensure_consistent_features(feats, list(FEATURE_COLUMNS))
    window_seconds = float(np.median(raw["window_seconds"]))
    split = chronological_split(feats, val_fraction=_VAL_FRACTION, test_fraction=_TEST_FRACTION)

    def xy(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
        return df, df["is_anomaly"].astype(int)

    x_train, y_train = xy(split.train)
    x_val, y_val = xy(split.val)
    x_test, y_test = xy(split.test)
    return DatasetSplits(
        x_train=x_train,
        y_train=y_train,
        x_val=x_val,
        y_val=y_val,
        x_test=x_test,
        y_test=y_test,
        feature_names=list(FEATURE_COLUMNS),
        window_seconds=window_seconds,
        n_windows=len(feats),
    )


def train_model(
    x_train: pd.DataFrame,
    y_train: pd.Series,
    model_type: str,
    seed: int,
    *,
    dataset_id: str = "",
) -> AnomalyDetector:
    if model_type not in _MODEL_FACTORIES:
        raise RetrainingError(f"unknown model_type {model_type!r}")
    _ensure_consistent_features(x_train, list(FEATURE_COLUMNS))
    detector = _MODEL_FACTORIES[model_type](list(FEATURE_COLUMNS), seed)
    detector.fit(x_train, y_train, random_seed=seed, training_dataset=dataset_id)
    return detector


def evaluate_model(
    detector: AnomalyDetector,
    x_test: pd.DataFrame,
    y_test: pd.Series,
    window_seconds: float,
) -> dict[str, Any]:
    scores = detector.score_samples(x_test)
    preds = detector.predict(x_test)
    result = evaluate(
        y_test.to_numpy(),
        preds,
        scores,
        threshold=detector.threshold_,
        window_seconds=window_seconds,
    )
    return result.to_dict()


def _kept_baseline_path(config: RetrainingConfig, version: str) -> Path | None:
    try:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        return MODELS_DIR / f"retrain__{config.dataset_id}__v{version}__baseline.joblib"
    except OSError as exc:  # read-only artefacts dir, etc.
        logger.warning("could not keep a local baseline copy (%s)", exc)
        return None


def retrain_pipeline(
    config: RetrainingConfig, *, progress: Callable[[str], None] = _noop
) -> RetrainingResult:
    set_global_seed(config.seed)

    data = load_dataset(config.dataset_id, config.seed)
    progress(
        f"Loaded dataset {config.dataset_id}... {data.n_windows} windows, "
        f"{len(data.feature_names)} features"
    )

    detector = train_model(
        data.x_train, data.y_train, config.model_type, config.seed, dataset_id=config.dataset_id
    )
    progress(f"Trained {config.model_type}")

    threshold = detector.calibrate_threshold(data.x_val, data.y_val, objective=_THRESHOLD_OBJECTIVE)
    progress(
        f"Calibrated threshold on validation... objective={_THRESHOLD_OBJECTIVE}, "
        f"threshold={threshold:.4f}"
    )

    metrics = evaluate_model(detector, data.x_test, data.y_test, data.window_seconds)
    pointwise = metrics["pointwise"]
    progress(
        f"Evaluated on test... F1={pointwise['f1']:.3f} recall={pointwise['recall']:.3f} "
        f"PR-AUC={pointwise.get('pr_auc', float('nan')):.3f}"
    )

    settings = get_mlflow_settings()
    ensure_local_tracking_store(settings.tracking_uri)
    baseline = freeze_baseline(
        data.x_train,
        feature_names=data.feature_names,
        model_version=f"retrain:{config.dataset_id}:{config.seed}",
        feature_schema_version=FEATURE_SCHEMA_VERSION,
    )

    with tempfile.TemporaryDirectory(prefix="retrain_", ignore_cleanup_errors=True) as tmp:
        model_path, baseline_tmp = _get_save_paths(tmp, config.model_type)
        detector.save(model_path)
        save_baseline(baseline, baseline_tmp)

        experiment_spec = _experiment_spec(config, data, detector, settings.experiment_name)
        extra_params = {
            "n_train": len(data.y_train),
            "n_train_anomaly": int(data.y_train.sum()),
            "n_eval": len(data.y_test),
            "n_eval_anomaly": int(data.y_test.sum()),
            "anomaly_fraction": pointwise.get("anomaly_fraction"),
        }

        run_id = log_run(
            experiment_spec,
            metrics,
            None,
            model_path,
            settings,
            extra_params=extra_params,
            baseline=baseline,
        )
        if run_id is None:
            raise RetrainingError(
                "MLflow is required for retraining (the run could not be logged) - "
                "set MLFLOW_TRACKING_URI to a reachable tracking store"
            )
        progress(f"Logged to MLflow... run_id={run_id}")

        candidate_version = register_model(str(model_path), run_id, settings)
        progress(f"Registered model... version={candidate_version}")

    try:
        set_model_baseline(settings, candidate_version, baseline)
    except Exception as exc:
        logger.warning("could not tag drift baseline on v%s (%s)", candidate_version, exc)

    kept_baseline = _kept_baseline_path(config, candidate_version)
    if kept_baseline is not None:
        save_baseline(baseline, kept_baseline)

    champion_metrics = get_champion_metrics(settings)
    champion_version: str | None = None
    try:
        champion_version, _run, _uri = resolve_alias(settings, CHAMPION_ALIAS)
    except Exception:
        champion_version = None

    decision = evaluate_candidate(
        _numeric_metrics(metrics),
        champion_metrics,
        config.policy or PromotionPolicy(),
        candidate_version=candidate_version,
        champion_version=champion_version,
    )
    against = f"v{champion_version}" if champion_version else "(no champion)"
    progress(f"Gate: {'PASS' if decision.promote else 'REJECT'} vs champion {against}")

    promoted = False
    if config.promote_if_passing and decision.promote:
        promote_model(
            settings,
            candidate_version,
            reason=(
                f"retrain dataset={config.dataset_id} seed={config.seed} "
                f"model={config.model_type}; " + "; ".join(decision.reasons)
            ),
            baseline=baseline,
        )
        promoted = True
        progress(f"Promoted candidate v{candidate_version} to champion")

    return RetrainingResult(
        candidate_version=candidate_version,
        promotion_decision=decision,
        metrics=metrics,
        run_id=run_id,
        champion_version=champion_version,
        promoted=promoted,
        baseline_path=kept_baseline,
    )


def _experiment_spec(
    config: RetrainingConfig,
    data: DatasetSplits,
    detector: AnomalyDetector,
    experiment_name: str,
) -> dict[str, Any]:
    hyperparameters = detector.metadata.hyperparameters if detector.metadata else {}
    train_pct = round((1 - _VAL_FRACTION - _TEST_FRACTION) * 100)
    return {
        "experiment": experiment_name,
        "run_name": f"retrain-{config.dataset_id}-seed{config.seed}-{config.model_type}",
        "workflow": "retrain",
        "dataset": config.dataset_id,
        "model_name": config.model_type,
        "model_type": config.model_type,
        "random_seed": config.seed,
        "threshold_objective": _THRESHOLD_OBJECTIVE,
        "split": (
            f"chronological {train_pct}/{round(_VAL_FRACTION * 100)}/{round(_TEST_FRACTION * 100)}"
        ),
        "feature_names": data.feature_names,
        "feature_count": len(data.feature_names),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "hyperparameters": hyperparameters,
    }


__all__ = [
    "SUPPORTED_MODEL_TYPES",
    "DatasetSplits",
    "RetrainingConfig",
    "RetrainingError",
    "RetrainingResult",
    "evaluate_model",
    "load_dataset",
    "retrain_pipeline",
    "train_model",
]
