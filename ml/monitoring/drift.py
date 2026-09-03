"""Deterministic data-drift detection via the Population Stability Index (PSI).

Compares a window of production features against a frozen
:class:`~ml.monitoring.baseline.BaselineDistribution` (ADR-034):

* per feature: bucket the current window with the baseline's bin edges, compute
  PSI against the baseline proportions, classify by the standard bands
  (``<0.1`` none, ``0.1-0.25`` moderate, ``>=0.25`` significant);
* overall: the most severe per-feature classification;
* prediction drift (relative change in anomaly rate) is reported **separately** —
  a distribution shift is not by itself model degradation.

No labels are used anywhere. No LLM. PSI values come only from the two
distributions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from ml.monitoring.baseline import BaselineDistribution, feature_statistics

# Standard PSI interpretation bands (Siddiqi; widely used in model monitoring).
DEFAULT_PSI_THRESHOLDS: tuple[float, float] = (0.10, 0.25)

# Floor applied to any zero proportion before the log ratio, so an emptied /
# newly-populated bin contributes a large-but-finite term instead of inf/nan.
_PSI_EPSILON = 1e-6

_CLASSIFICATION_RANK = {"none": 0, "moderate": 1, "significant": 2}
_OVERALL = {0: "no_drift", 1: "moderate_drift", 2: "significant_drift"}
_DECISION = {"none": "pass", "moderate": "warn", "significant": "fail"}


class DriftError(ValueError):
    """Invalid input to drift detection."""


@dataclass
class FeatureDriftReport:
    feature_name: str
    psi: float
    classification: str  # none | moderate | significant
    decision: str  # pass | warn | fail
    threshold_moderate: float
    threshold_significant: float
    reference_stats: dict[str, float]
    current_stats: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DriftReport:
    feature_reports: list[FeatureDriftReport]
    overall_decision: str  # no_drift | moderate_drift | significant_drift
    prediction_drift: float | None
    model_version: str
    n_samples_current: int
    missing_features: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["feature_reports"] = [r.to_dict() for r in self.feature_reports]
        return data

    @property
    def drift_detected(self) -> bool:
        return self.overall_decision != "no_drift"


def calculate_psi(
    expected_proportions: list[float] | np.ndarray,
    actual_proportions: list[float] | np.ndarray,
) -> float:
    """PSI = Σ (actual_i - expected_i) · ln(actual_i / expected_i).

    Both inputs are proportion vectors of equal length. Zeros are floored to a
    small epsilon (not renormalised — PSI is a heuristic index)."""

    expected = np.asarray(expected_proportions, dtype=float)
    actual = np.asarray(actual_proportions, dtype=float)
    if expected.shape != actual.shape or expected.ndim != 1:
        raise DriftError(
            f"proportion vectors must be 1-D and equal length, got "
            f"{expected.shape} and {actual.shape}"
        )
    expected = np.clip(expected, _PSI_EPSILON, None)
    actual = np.clip(actual, _PSI_EPSILON, None)
    return float(np.sum((actual - expected) * np.log(actual / expected)))


def classify_psi(psi: float, thresholds: tuple[float, float] = DEFAULT_PSI_THRESHOLDS) -> str:
    moderate, significant = thresholds
    if psi < moderate:
        return "none"
    if psi < significant:
        return "moderate"
    return "significant"


def compute_prediction_drift(prev_rate: float, current_rate: float) -> float | None:
    """Relative change in anomaly rate: ``(current - prev) / prev``. ``None`` when
    there is no prior rate to compare against."""

    if prev_rate <= 0:
        return None
    return (current_rate - prev_rate) / prev_rate


def _current_proportions(values: np.ndarray, edges: list[float]) -> list[float]:
    edge_arr = np.asarray(edges, dtype=float)
    clipped = np.clip(values, edge_arr[0], edge_arr[-1])
    counts, _ = np.histogram(clipped, bins=edge_arr)
    total = float(counts.sum())
    if total <= 0:
        return [1.0 / len(counts)] * len(counts)
    return [float(c) / total for c in counts]


def detect_drift(
    x_current: pd.DataFrame | np.ndarray,
    baseline: BaselineDistribution,
    model_version: str | None = None,
    *,
    psi_thresholds: tuple[float, float] | None = None,
    prediction_rate_previous: float | None = None,
    prediction_rate_current: float | None = None,
) -> DriftReport:
    """Compare ``x_current`` (production features only) against ``baseline``.

    Features present in the baseline but absent from ``x_current`` are reported in
    ``missing_features`` and skipped; extra columns in ``x_current`` are ignored.
    """

    thresholds = psi_thresholds or DEFAULT_PSI_THRESHOLDS

    if isinstance(x_current, pd.DataFrame):
        frame = x_current
        present = [f for f in baseline.feature_names if f in frame.columns]
    else:
        arr = np.asarray(x_current, dtype=float)
        if arr.ndim != 2 or arr.shape[1] != len(baseline.feature_names):
            raise DriftError(
                f"array shape {arr.shape} does not match baseline's "
                f"{len(baseline.feature_names)} features"
            )
        frame = pd.DataFrame(arr, columns=baseline.feature_names)
        present = list(baseline.feature_names)

    if len(frame) == 0:
        raise DriftError("current feature frame has no rows")
    if not present:
        raise DriftError("no baseline features present in the current data")

    missing = [f for f in baseline.feature_names if f not in present]

    reports: list[FeatureDriftReport] = []
    for name in present:
        col = frame[name].to_numpy(dtype=float)
        actual = _current_proportions(col, baseline.bin_edges[name])
        psi = calculate_psi(baseline.reference_proportions[name], actual)
        classification = classify_psi(psi, thresholds)
        reports.append(
            FeatureDriftReport(
                feature_name=name,
                psi=round(psi, 6),
                classification=classification,
                decision=_DECISION[classification],
                threshold_moderate=thresholds[0],
                threshold_significant=thresholds[1],
                reference_stats=baseline.statistics.get(name, {}),
                current_stats=feature_statistics(col),
            )
        )

    worst = max((_CLASSIFICATION_RANK[r.classification] for r in reports), default=0)

    prediction_drift: float | None = None
    if prediction_rate_previous is not None and prediction_rate_current is not None:
        prediction_drift = compute_prediction_drift(
            prediction_rate_previous, prediction_rate_current
        )

    return DriftReport(
        feature_reports=reports,
        overall_decision=_OVERALL[worst],
        prediction_drift=prediction_drift,
        model_version=str(model_version if model_version is not None else baseline.model_version),
        n_samples_current=len(frame),
        missing_features=missing,
    )
