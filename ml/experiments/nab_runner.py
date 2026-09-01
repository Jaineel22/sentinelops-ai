"""Track B experiment runner (NAB).

NAB series have unrelated value ranges and dynamics, so each is fitted,
calibrated, and evaluated **on its own** (its own chronological split and
threshold). The report gives per-series metrics plus a macro-average — the
standard way to summarise a benchmark family.

This is a methodology check (ADR-004 / ADR-013): it never trains a model that is
then used on Track A telemetry.
"""

from __future__ import annotations

import json
import platform
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from ml import __version__
from ml.config import RANDOM_SEED, REPORTS_DIR, set_global_seed
from ml.data.nab import NAB_FAMILIES, NAB_FEATURE_COLUMNS, build_nab_features, load_nab_series
from ml.evaluation.metrics import evaluate
from ml.experiments.runner import ModelFactory, _git_sha
from ml.models import IsolationForestDetector, RobustZScoreDetector
from ml.splits import chronological_split


@dataclass
class NabExperimentSpec:
    name: str
    title: str
    rationale: str
    family: str
    models: dict[str, ModelFactory]
    target_fpr: float = 0.05


def _default_models() -> dict[str, ModelFactory]:
    fn = list(NAB_FEATURE_COLUMNS)
    return {
        "robust_zscore": lambda names: RobustZScoreDetector(feature_names=names or fn),
        "isolation_forest": lambda names: IsolationForestDetector(
            feature_names=names or fn, random_state=RANDOM_SEED, n_estimators=200
        ),
    }


def nab_experiment_specs() -> dict[str, NabExperimentSpec]:
    specs = [
        NabExperimentSpec(
            name="exp5_nab_realknowncause",
            title="Exp 5 - Methodology on NAB (realKnownCause)",
            rationale="Independent benchmark: run the same robust-z-score baseline and "
            "Isolation Forest (on engineered rolling features) per series, with each "
            "threshold calibrated to a 5% false-positive budget. Does the methodology "
            "transfer to real-world anomaly series unrelated to our fault generator?",
            family="realKnownCause",
            models=_default_models(),
        ),
        NabExperimentSpec(
            name="exp6_nab_realawscloudwatch",
            title="Exp 6 - Methodology on NAB (realAWSCloudwatch)",
            rationale="Second independent NAB family (EC2 CPU utilisation), same "
            "per-series methodology and 5% FPR budget.",
            family="realAWSCloudwatch",
            models=_default_models(),
        ),
    ]
    return {s.name: s for s in specs}


def _macro_average(per_series: dict[str, dict[str, Any]]) -> dict[str, float]:
    keys = ["precision", "recall", "f1", "false_positive_rate", "pr_auc", "roc_auc"]
    out: dict[str, float] = {}
    for k in keys:
        vals = [m[k] for m in per_series.values() if k in m and m[k] is not None]
        out[k] = round(float(np.mean(vals)), 4) if vals else float("nan")
    return out


def run_nab_experiment(spec: NabExperimentSpec, *, seed: int = RANDOM_SEED) -> dict[str, Any]:
    set_global_seed(seed)
    report_dir = REPORTS_DIR / spec.name
    report_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, Any] = {}
    for model_name, factory in spec.models.items():
        per_series: dict[str, dict[str, Any]] = {}
        for rel in NAB_FAMILIES[spec.family]:
            feats = build_nab_features(load_nab_series(rel))
            # NAB-style: a short early "probationary" train slice to learn normal,
            # a small validation slice for the threshold, and score the long tail
            # (where NAB's labelled anomalies mostly fall).
            split = chronological_split(feats, val_fraction=0.15, test_fraction=0.65)

            detector = factory(list(NAB_FEATURE_COLUMNS))
            detector.fit(
                split.train,
                split.train["is_anomaly"],
                random_seed=seed,
                training_dataset=f"NAB/{rel}",
            )
            detector.calibrate_threshold(
                split.val, split.val["is_anomaly"], objective="fpr", target_fpr=spec.target_fpr
            )
            scores = detector.score_samples(split.test)
            preds = detector.predict(split.test)
            ev = evaluate(
                split.test["is_anomaly"].to_numpy(),
                preds,
                scores,
                threshold=detector.threshold_,
                window_seconds=300.0,
            )
            per_series[rel] = {
                **ev.pointwise,
                "event_recall": ev.eventwise["event_recall"],
                "n_test": len(split.test),
            }

        results[model_name] = {
            "supervised": False,
            "per_series": per_series,
            "macro_average": _macro_average(per_series),
        }

    report = {
        "experiment": spec.name,
        "title": spec.title,
        "rationale": spec.rationale,
        "track": "B (NAB benchmark)",
        "family": spec.family,
        "series": NAB_FAMILIES[spec.family],
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "git_sha": _git_sha(),
        "ml_version": __version__,
        "python": platform.python_version(),
        "random_seed": seed,
        "feature_names": list(NAB_FEATURE_COLUMNS),
        "evaluation": "per-series chronological 20/15/65 split (short probationary "
        f"train); threshold calibrated to <= {spec.target_fpr:.0%} FPR on validation; "
        "macro-averaged across series",
        "results": results,
    }
    (report_dir / "metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    _plot_nab(spec, results, report_dir)

    print(f"[{spec.name}] -> {report_dir / 'metrics.json'}")
    for model_name, res in results.items():
        ma = res["macro_average"]
        print(
            f"    {model_name:<20} macro  P={ma['precision']:.3f} R={ma['recall']:.3f} "
            f"F1={ma['f1']:.3f} PR-AUC={ma['pr_auc']:.3f} ROC-AUC={ma['roc_auc']:.3f}"
        )
    return report


def _plot_nab(spec: NabExperimentSpec, results: dict[str, Any], report_dir: Path) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:
        print(f"    (plots skipped: {exc})")
        return

    models = list(results)
    series = NAB_FAMILIES[spec.family]
    fig, ax = plt.subplots(figsize=(2 + 1.4 * len(series), 4))
    width = 0.8 / max(len(models), 1)
    x = np.arange(len(series))
    for i, m in enumerate(models):
        vals = [results[m]["per_series"][s].get("pr_auc") or 0.0 for s in series]
        ax.bar(x + i * width, vals, width, label=m)
    ax.axhline(0.1, color="grey", ls=":", lw=1, label="≈ base rate")
    ax.set_xticks(x + width * (len(models) - 1) / 2)
    ax.set_xticklabels(
        [s.split("/")[-1].replace(".csv", "") for s in series], rotation=20, ha="right", fontsize=7
    )
    ax.set_ylabel("PR-AUC (test)")
    ax.set_title(f"{spec.title} - per-series PR-AUC")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(report_dir / "per_series_pr_auc.png", dpi=110)
    plt.close(fig)
