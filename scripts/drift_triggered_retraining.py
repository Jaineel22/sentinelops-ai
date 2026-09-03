"""Phase 6E demo: drift detected -> retrain -> evaluate -> promote / reject.

    1. resolve the champion and its frozen drift baseline (6D)
    2. score a window of "current" telemetry against it (``run_b`` stands in for
       drifted production data)
    3. if drift is *significant*, retrain on that data (reusing the Phase 2
       pipeline) and run the candidate through the promotion gate (6B)

Nothing is auto-promoted here (retraining is CLI-driven, not autonomous). If no
champion / baseline exists yet the script bootstraps one (retrain ``run_a`` +
promote) so the drift check has a reference.

Needs a reachable MLflow tracking store (``MLFLOW_TRACKING_URI``).
"""

from __future__ import annotations

import sys

import pandas as pd
from ml.data.prepare import load_processed_run
from ml.data.schema import FEATURE_COLUMNS
from ml.features.engineering import build_features
from ml.mlops.config import get_mlflow_settings
from ml.mlops.registry import CHAMPION_ALIAS, get_model_baseline, resolve_alias
from ml.mlops.retraining import RetrainingConfig, RetrainingError, retrain_pipeline
from ml.monitoring.baseline import BaselineDistribution
from ml.monitoring.drift import detect_drift


def _current_features(dataset_id: str) -> pd.DataFrame:
    return build_features(load_processed_run(dataset_id))[list(FEATURE_COLUMNS)]


def _bootstrap_champion() -> str | None:
    print("no champion baseline yet - bootstrapping (retrain run_a + promote)...")
    try:
        result = retrain_pipeline(
            RetrainingConfig(dataset_id="run_a", seed=42, promote_if_passing=True),
            progress=lambda message: print(f"  {message}"),
        )
    except RetrainingError as exc:
        print(f"bootstrap failed: {exc}")
        return None
    return result.candidate_version


def main() -> int:
    settings = get_mlflow_settings()

    champion_version: str | None = None
    baseline: BaselineDistribution | None = None
    try:
        champion_version, _run, _uri = resolve_alias(settings, CHAMPION_ALIAS)
        baseline = get_model_baseline(settings, champion_version)
    except Exception:
        champion_version, baseline = None, None

    if baseline is None:
        champion_version = _bootstrap_champion()
        if champion_version is None:
            return 2
        baseline = get_model_baseline(settings, champion_version)
        if baseline is None:
            print("still no baseline after bootstrap - aborting")
            return 2

    current = _current_features("run_b")
    report = detect_drift(current, baseline, model_version=champion_version)
    print(
        f"\ndrift vs champion v{champion_version}: {report.overall_decision} "
        f"(n={report.n_samples_current})"
    )
    for feature in sorted(report.feature_reports, key=lambda r: r.psi, reverse=True)[:5]:
        print(f"  {feature.feature_name:<30} PSI={feature.psi:.3f}  {feature.classification}")

    if report.overall_decision != "significant_drift":
        print("\ndrift is not significant - no retraining triggered")
        return 0

    print("\nsignificant drift -> retraining on run_b...")
    try:
        result = retrain_pipeline(
            RetrainingConfig(dataset_id="run_b", seed=42),
            progress=lambda message: print(f"  {message}"),
        )
    except RetrainingError as exc:
        print(f"retraining failed: {exc}")
        return 2

    decision = result.promotion_decision
    print(
        f"\ncandidate v{result.candidate_version}: "
        f"{'PASS' if decision.promote else 'REJECT'} (champion "
        f"{('v' + result.champion_version) if result.champion_version else 'none'})"
    )
    for reason in decision.reasons:
        print(f"  - {reason}")
    if decision.promote:
        print(
            f"to promote: python -m ml.mlops promote --candidate-version {result.candidate_version}"
        )
    return 0 if decision.promote else 1


if __name__ == "__main__":
    sys.exit(main())
