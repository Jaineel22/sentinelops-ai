"""Phase 6 end-to-end demo - the whole MLOps lifecycle in one deterministic run.

    train champion (run_a)  ->  register + promote (6B)
      ->  load champion from the registry (6C)
      ->  detect drift on run_b (6D)
      ->  retrain on run_b (6E)  ->  evaluate candidate vs champion (6B gate)
      ->  promote or reject

Needs no server: with ``MLFLOW_TRACKING_URI`` unset it uses a throwaway local
sqlite store (printed at the end so you can open it with ``mlflow ui``). Set
``MLFLOW_TRACKING_URI`` (e.g. ``http://localhost:5000``) to run against a shared
store. Deterministic - seed 42 throughout.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def _bootstrap_tracking_uri() -> str:
    uri = os.environ.get("MLFLOW_TRACKING_URI")
    if uri:
        return uri
    store = Path(tempfile.mkdtemp(prefix="phase6_demo_")) / "phase6_demo.db"
    uri = f"sqlite:///{store.as_posix()}"
    os.environ["MLFLOW_TRACKING_URI"] = uri
    os.environ.setdefault("MLFLOW_REGISTERED_MODEL_NAME", "sentinelops-anomaly-detector")
    os.environ.setdefault("MLFLOW_EXPERIMENT_NAME", "phase6-e2e-demo")
    return uri


def main() -> int:
    tracking_uri = _bootstrap_tracking_uri()

    # imported after the env is set so get_mlflow_settings() caches the right URI
    from ml.data.prepare import load_processed_run
    from ml.data.schema import FEATURE_COLUMNS
    from ml.features.engineering import build_features
    from ml.inference import DetectorService
    from ml.mlops.config import ensure_local_tracking_store, get_mlflow_settings
    from ml.mlops.promotion import promote_model
    from ml.mlops.registry import (
        CHAMPION_ALIAS,
        PREVIOUS_CHAMPION_ALIAS,
        get_model_baseline,
        get_registered_aliases,
        resolve_alias,
    )
    from ml.mlops.retraining import RetrainingConfig, RetrainingError, retrain_pipeline
    from ml.monitoring.drift import detect_drift

    ensure_local_tracking_store(tracking_uri)
    settings = get_mlflow_settings()

    print("=== Phase 6 End-to-End Demo ===\n")
    print(f"tracking store: {tracking_uri}\n")

    # 1. train + promote a champion on run_a
    print("1. Training champion on run_a...")
    try:
        champion = retrain_pipeline(
            RetrainingConfig(dataset_id="run_a", seed=42, promote_if_passing=True)
        )
    except RetrainingError as exc:
        print(f"   FAILED: {exc}")
        return 2
    champ_pw = champion.metrics["pointwise"]
    print(
        f"   -> Champion v{champion.candidate_version}: F1={champ_pw['f1']:.3f}, "
        f"Recall={champ_pw['recall']:.3f}, PR-AUC={champ_pw.get('pr_auc', float('nan')):.3f}"
    )

    # 2. load the champion back through the registry (what /ready reports)
    print("\n2. Loading champion via registry...")
    service = DetectorService.from_registry(settings)
    print(f"   -> model_source: {service.source}, model_version: {service.model_version}")

    # 3. drift detection: champion baseline vs "current" data (run_b)
    print("\n3. Detecting drift on run_b...")
    baseline = get_model_baseline(settings, champion.candidate_version)
    if baseline is None:
        print("   FAILED: champion has no drift baseline")
        return 2
    current = build_features(load_processed_run("run_b"))[list(FEATURE_COLUMNS)]
    report = detect_drift(current, baseline, model_version=champion.candidate_version)
    counts = {"significant": 0, "moderate": 0, "none": 0}
    for feature in report.feature_reports:
        counts[feature.classification] += 1
    print(f"   -> overall: {report.overall_decision} (n={report.n_samples_current})")
    print(
        f"   -> {counts['significant']} features significant, "
        f"{counts['moderate']} moderate, {counts['none']} none"
    )

    # 4. retrain on the drifted data (no auto-promote - show the gate first)
    print("\n4. Retraining on run_b...")
    try:
        candidate = retrain_pipeline(RetrainingConfig(dataset_id="run_b", seed=42))
    except RetrainingError as exc:
        print(f"   FAILED: {exc}")
        return 2
    cand_pw = candidate.metrics["pointwise"]
    print(
        f"   -> Candidate v{candidate.candidate_version}: F1={cand_pw['f1']:.3f}, "
        f"Recall={cand_pw['recall']:.3f}, PR-AUC={cand_pw.get('pr_auc', float('nan')):.3f}"
    )

    # 5. the gate decision (already computed by retrain_pipeline)
    print("\n5. Evaluating candidate vs champion...")
    decision = candidate.promotion_decision
    delta = cand_pw["f1"] - champ_pw["f1"]
    print(f"   -> F1 change vs champion: {delta:+.3f} (tolerance: 0.05)")
    for reason in decision.reasons:
        print(f"   -> {reason}")
    print(f"   -> gate: {'PASS' if decision.promote else 'REJECT'}")

    # 6. promote (or not)
    print("\n6. Promoting candidate..." if decision.promote else "\n6. Candidate rejected...")
    if decision.promote:
        promote_model(
            settings,
            candidate.candidate_version,
            reason="phase6 e2e demo",
            baseline=get_model_baseline(settings, candidate.candidate_version),
        )
        aliases = get_registered_aliases(settings)
        for version, names in sorted(aliases.items(), key=lambda kv: int(kv[0])):
            for name in sorted(names):
                print(f"   -> {name} -> version {version}")
        new_champion = resolve_alias(settings, CHAMPION_ALIAS)[0]
        prev = None
        try:
            prev = resolve_alias(settings, PREVIOUS_CHAMPION_ALIAS)[0]
        except Exception:
            prev = None
        ok = new_champion == candidate.candidate_version and prev == champion.candidate_version
    else:
        current_champion = resolve_alias(settings, CHAMPION_ALIAS)[0]
        print(f"   -> champion unchanged: v{current_champion}")
        ok = current_champion == champion.candidate_version

    print()
    if ok:
        print("PASS - Phase 6 end-to-end flow verified.")
        return 0
    print("FAIL - the lifecycle did not end in the expected state.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
