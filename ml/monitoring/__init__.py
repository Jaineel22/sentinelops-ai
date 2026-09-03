"""SentinelOps AI - model / data monitoring (Phase 6D).

Deterministic, label-free **data-drift detection** for the anomaly detector.

* :func:`freeze_baseline` snapshots a model's *training* feature distribution as
  a :class:`BaselineDistribution` (per-feature quantile bins + summary stats),
  stored as an artifact next to the model version at training / promotion time.
* :func:`detect_drift` compares a later window of *production* features against
  that baseline with the Population Stability Index (PSI), returning a
  :class:`DriftReport` with a per-feature breakdown and an overall
  ``no_drift`` / ``moderate_drift`` / ``significant_drift`` decision.

Prediction drift (change in anomaly rate) is reported separately from feature
drift; neither on its own is evidence of model *performance* degradation, which
needs ground-truth labels. Methodology + limitations: ADR-034.
"""

from __future__ import annotations

from ml.monitoring.baseline import (
    BaselineDistribution,
    freeze_baseline,
    load_baseline,
    save_baseline,
)
from ml.monitoring.drift import (
    DEFAULT_PSI_THRESHOLDS,
    DriftReport,
    FeatureDriftReport,
    calculate_psi,
    classify_psi,
    compute_prediction_drift,
    detect_drift,
)

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_PSI_THRESHOLDS",
    "BaselineDistribution",
    "DriftReport",
    "FeatureDriftReport",
    "calculate_psi",
    "classify_psi",
    "compute_prediction_drift",
    "detect_drift",
    "freeze_baseline",
    "load_baseline",
    "save_baseline",
]
