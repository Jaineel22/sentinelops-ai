"""Experiment execution: fit -> calibrate on validation -> evaluate on test,
then persist model + metrics + plots.

The runner never touches the test labels except for the final evaluation. The
threshold is always chosen on validation.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ml import __version__
from ml.config import MODELS_DIR, RANDOM_SEED, REPORTS_DIR, set_global_seed
from ml.evaluation.metrics import evaluate
from ml.models.base import AnomalyDetector


@dataclass
class PreparedData:
    x_train: pd.DataFrame
    y_train: pd.Series
    x_val: pd.DataFrame
    y_val: pd.Series
    x_test: pd.DataFrame
    y_test: pd.Series
    window_seconds: float
    feature_names: list[str]
    notes: dict[str, Any] = field(default_factory=dict)


ModelFactory = Callable[[list[str]], AnomalyDetector]


@dataclass
class ExperimentSpec:
    name: str
    title: str
    rationale: str
    build_data: Callable[[], PreparedData]
    models: dict[str, ModelFactory]  # factory receives the feature-name list
    threshold_objective: str = "f1"
    supervised_models: frozenset[str] = frozenset()


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "unknown"


def run_experiment(spec: ExperimentSpec, *, seed: int = RANDOM_SEED) -> dict[str, Any]:
    set_global_seed(seed)
    data = spec.build_data()

    report_dir = REPORTS_DIR / spec.name
    report_dir.mkdir(parents=True, exist_ok=True)

    per_model: dict[str, Any] = {}
    scored_test: dict[str, np.ndarray] = {}

    for model_name, factory in spec.models.items():
        detector = factory(list(data.feature_names))
        supervised = model_name in spec.supervised_models
        # All detectors receive y_train: the supervised model learns from it;
        # the unsupervised ones fit on the y==0 (normal) subset (see phase-2.md §8-9).
        detector.fit(
            data.x_train,
            data.y_train,
            random_seed=seed,
            training_dataset=str(data.notes.get("dataset", spec.name)),
        )
        detector.calibrate_threshold(data.x_val, data.y_val, objective=spec.threshold_objective)

        scores = detector.score_samples(data.x_test)
        preds = detector.predict(data.x_test)
        result = evaluate(
            data.y_test.to_numpy(),
            preds,
            scores,
            threshold=detector.threshold_,
            window_seconds=data.window_seconds,
        )
        scored_test[model_name] = scores

        model_path = MODELS_DIR / f"{spec.name}__{model_name}.joblib"
        detector.save(model_path)

        per_model[model_name] = {
            "supervised": supervised,
            "metadata": detector.metadata.to_dict() if detector.metadata else None,
            "model_path": str(model_path.relative_to(MODELS_DIR.parent.parent)),
            **result.to_dict(),
        }

    report = {
        "experiment": spec.name,
        "title": spec.title,
        "rationale": spec.rationale,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "git_sha": _git_sha(),
        "ml_version": __version__,
        "python": platform.python_version(),
        "random_seed": seed,
        "threshold_objective": spec.threshold_objective,
        "feature_names": data.feature_names,
        "split_sizes": {
            "train": {"n": len(data.y_train), "anomaly": int(data.y_train.sum())},
            "val": {"n": len(data.y_val), "anomaly": int(data.y_val.sum())},
            "test": {"n": len(data.y_test), "anomaly": int(data.y_test.sum())},
        },
        "notes": data.notes,
        "results": per_model,
    }
    (report_dir / "metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    # Phase 6D: freeze the training-feature distribution as a drift baseline
    # (labels excluded). Saved next to the models and, if MLflow is configured,
    # logged with each run so a registered model version carries its baseline.
    baseline = _freeze_experiment_baseline(spec, data)

    _write_plots(spec, data, scored_test, report_dir)
    print(f"[{spec.name}] -> {report_dir / 'metrics.json'}")
    for name, res in per_model.items():
        pw = res["pointwise"]
        print(
            f"    {name:<28} P={pw['precision']:.3f} R={pw['recall']:.3f} "
            f"F1={pw['f1']:.3f} FPR={pw['false_positive_rate']:.3f} "
            f"PR-AUC={pw.get('pr_auc', float('nan')):.3f}"
        )

    # Phase 6A: mirror each model's run into MLflow when a tracking URI is
    # configured. Opt-in (env var present) so the offline pipeline, CI, and the
    # reproducibility tests stay side-effect-free by default. `metrics.json` and
    # `summary.md` above are the authoritative record and are already written.
    if os.environ.get("MLFLOW_TRACKING_URI"):
        _log_experiment_to_mlflow(spec, report, report_dir, baseline)

    return report


def _freeze_experiment_baseline(spec: ExperimentSpec, data: PreparedData) -> Any:
    """Best-effort drift baseline from the training features. Returns the
    ``BaselineDistribution`` (or ``None`` on any failure) and saves it under
    ``MODELS_DIR``."""

    try:
        from ml.data.schema import FEATURE_SCHEMA_VERSION
        from ml.monitoring.baseline import freeze_baseline, save_baseline

        baseline = freeze_baseline(
            data.x_train,
            feature_names=list(data.feature_names),
            model_version=spec.name,
            feature_schema_version=FEATURE_SCHEMA_VERSION,
        )
        save_baseline(baseline, MODELS_DIR / f"{spec.name}__baseline.joblib")
        return baseline
    except Exception as exc:  # monitoring is additive - never fail an experiment
        print(f"    (drift baseline skipped: {exc})")
        return None


def _log_experiment_to_mlflow(
    spec: ExperimentSpec, report: dict[str, Any], report_dir: Path, baseline: Any = None
) -> None:
    """Best-effort: one MLflow run per model in the experiment. Never raises — a
    tracking failure must not fail an experiment."""

    try:
        from ml.mlops.config import MLflowSettings
        from ml.mlops.tracking import log_run
    except ImportError:
        print("    (mlflow tracking skipped: ml.mlops / mlflow-skinny not installed)")
        return

    settings = MLflowSettings()
    notes = report.get("notes", {})
    splits = report.get("split_sizes", {})
    for model_name, res in report["results"].items():
        meta = res.get("metadata") or {}
        model_path = MODELS_DIR / f"{spec.name}__{model_name}.joblib"
        experiment_spec: dict[str, Any] = {
            "experiment": spec.name,
            "run_name": f"{spec.name}__{model_name}",
            "title": spec.title,
            "model_name": model_name,
            "model_type": meta.get("model_type", model_name),
            "supervised": res.get("supervised", False),
            "dataset": notes.get("dataset", spec.name),
            "split": notes.get("split", ""),
            "random_seed": report.get("random_seed"),
            "threshold_objective": report.get("threshold_objective"),
            "feature_names": report.get("feature_names", []),
            "feature_count": len(report.get("feature_names", [])),
            "hyperparameters": meta.get("hyperparameters", {}),
        }
        pointwise = res.get("pointwise", {})
        extra_params: dict[str, Any] = {
            "n_train": splits.get("train", {}).get("n"),
            "n_train_anomaly": splits.get("train", {}).get("anomaly"),
            "n_eval": splits.get("test", {}).get("n"),
            "n_eval_anomaly": splits.get("test", {}).get("anomaly"),
            "anomaly_fraction": pointwise.get("anomaly_fraction"),
        }
        metrics = {
            "threshold": res.get("threshold"),
            "pointwise": pointwise,
            "eventwise": res.get("eventwise", {}),
        }
        run_id = log_run(
            experiment_spec,
            metrics,
            report_dir,
            model_path,
            settings,
            extra_params=extra_params,
            baseline=baseline,
        )
        if run_id:
            print(f"    mlflow run [{model_name}]: {run_id}")


def _write_plots(
    spec: ExperimentSpec,
    data: PreparedData,
    scored_test: dict[str, np.ndarray],
    report_dir: Path,
) -> None:
    try:
        from ml.evaluation.plots import (
            plot_metric_comparison,
            plot_score_timeline,
        )
    except Exception as exc:
        print(f"    (plots skipped: {exc})")
        return

    metrics_path = report_dir / "metrics.json"
    report = json.loads(metrics_path.read_text())
    plot_metric_comparison(
        report["results"], report_dir / "metric_comparison.png", title=spec.title
    )
    for model_name, scores in scored_test.items():
        threshold = report["results"][model_name]["threshold"]
        plot_score_timeline(
            data.y_test.to_numpy(),
            scores,
            threshold,
            report_dir / f"timeline_{model_name}.png",
            title=f"{spec.title} - {model_name}",
        )
