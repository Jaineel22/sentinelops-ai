"""Phase 6E demo: retrain the anomaly detector and run it through the promotion gate.

Deterministic (seed 42 on ``run_a``). Reuses the whole Phase 2 pipeline (data →
features → train → calibrate on validation → evaluate on test), logs the run to
MLflow (6A), registers a new model version (6B), and compares it against the
current champion with the deterministic gate (6B). It does **not** promote.

Needs a reachable MLflow tracking store:

    MLFLOW_TRACKING_URI=sqlite:///mlruns/mlflow.db python scripts/retraining_demo.py
    # or, against the compose server:
    MLFLOW_TRACKING_URI=http://localhost:5000  python scripts/retraining_demo.py
"""

from __future__ import annotations

import sys

from ml.mlops.retraining import RetrainingConfig, RetrainingError, retrain_pipeline


def main() -> int:
    config = RetrainingConfig(dataset_id="run_a", seed=42)
    print(f"=== Retraining demo: dataset={config.dataset_id}, seed={config.seed} ===")
    try:
        result = retrain_pipeline(config, progress=lambda message: print(f"  {message}"))
    except RetrainingError as exc:
        print(f"\nretraining could not run: {exc}")
        return 2

    pointwise = result.metrics["pointwise"]
    decision = result.promotion_decision
    print()
    print(f"candidate version : {result.candidate_version}")
    print(f"champion version  : {result.champion_version or '(none)'}")
    print(f"run id            : {result.run_id}")
    print(
        f"candidate metrics : F1={pointwise['f1']:.3f}  recall={pointwise['recall']:.3f}  "
        f"PR-AUC={pointwise.get('pr_auc', float('nan')):.3f}"
    )
    print(f"gate decision     : {'PASS' if decision.promote else 'REJECT'}")
    for reason in decision.reasons:
        print(f"  - {reason}")
    print(f"baseline saved to : {result.baseline_path or '(not kept)'}")
    if decision.promote:
        version = result.candidate_version
        print(f"\nto promote: python -m ml.mlops promote --candidate-version {version}")
    return 0 if decision.promote else 1


if __name__ == "__main__":
    sys.exit(main())
