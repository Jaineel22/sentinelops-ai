"""SentinelOps AI — MLOps lifecycle (Phase 6).

Wraps the existing Phase 2 training/evaluation pipeline (``ml.experiments``,
``ml.models``, ``ml.evaluation``) with an MLflow-backed lifecycle:

* experiment tracking — every training run's parameters, metrics, artifacts and
  lineage are recorded in MLflow (Sub-phase 6A);
* model registry + alias-based promotion — ``candidate`` / ``champion`` model
  versions, promoted only through a deterministic evaluation gate (6B);
* inference integration — the anomaly-detector resolves a model by alias (6C);
* monitoring + drift detection (6D) and a reproducible retraining workflow (6E).

The Phase 2 evaluation framework stays authoritative: MLflow *records* its
results, it never replaces it. MLflow logging is additive and fail-safe — the
training pipeline runs unchanged whether or not MLflow is installed or reachable.
"""

from __future__ import annotations

from ml.mlops.retraining import (
    RetrainingConfig,
    RetrainingError,
    RetrainingResult,
    retrain_pipeline,
)

__version__ = "0.1.0"

__all__ = [
    "RetrainingConfig",
    "RetrainingError",
    "RetrainingResult",
    "retrain_pipeline",
]
