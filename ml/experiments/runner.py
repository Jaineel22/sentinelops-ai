"""Experiment execution: fit -> calibrate on validation -> evaluate on test,
then persist model + metrics + plots.

The runner never touches the test labels except for the final evaluation. The
threshold is always chosen on validation.
"""

from __future__ import annotations

import json
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

    _write_plots(spec, data, scored_test, report_dir)
    print(f"[{spec.name}] -> {report_dir / 'metrics.json'}")
    for name, res in per_model.items():
        pw = res["pointwise"]
        print(
            f"    {name:<28} P={pw['precision']:.3f} R={pw['recall']:.3f} "
            f"F1={pw['f1']:.3f} FPR={pw['false_positive_rate']:.3f} "
            f"PR-AUC={pw.get('pr_auc', float('nan')):.3f}"
        )
    return report


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
