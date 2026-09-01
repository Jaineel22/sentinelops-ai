"""Statistical baseline: robust multivariate z-score (median / MAD).

Why this baseline: anomaly datasets are imbalanced and often unlabelled, so the
baseline must be unsupervised and must not assume Gaussian, outlier-free
training data. Median + MAD (median absolute deviation) is the standard robust
alternative to mean + std — a few contaminated training windows barely move it.

Score for a window = the largest robust z-score across features:

    z_j = |x_j - median_j| / (1.4826 * MAD_j)
    score = max_j z_j

A high score means at least one signal is far from its typical range. The
threshold is calibrated on validation data (see ``AnomalyDetector``).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ml.models.base import AnomalyDetector

_MAD_SCALE = 1.4826  # makes MAD a consistent estimator of std for normal data


class RobustZScoreDetector(AnomalyDetector):
    def __init__(self, *, feature_names: list[str] | None = None, min_scale: float = 1e-9) -> None:
        super().__init__(feature_names=feature_names)
        self.min_scale = min_scale
        self.center_: np.ndarray | None = None
        self.scale_: np.ndarray | None = None

    @property
    def model_type(self) -> str:
        return "robust_zscore"

    def _hyperparameters(self) -> dict[str, Any]:
        return {"mad_scale": _MAD_SCALE, "min_scale": self.min_scale, "aggregation": "max_abs_z"}

    def _fit(self, X: np.ndarray, y: np.ndarray | None) -> None:
        # Fit on the "normal" rows when labels are available, else on everything.
        ref = X[y == 0] if y is not None and (y == 0).any() else X
        self.center_ = np.median(ref, axis=0)
        mad = np.median(np.abs(ref - self.center_), axis=0) * _MAD_SCALE
        # Constant feature (MAD == 0): clamp scale so it contributes z == 0.
        self.scale_ = np.where(mad < self.min_scale, np.inf, mad)

    def _score(self, X: np.ndarray) -> np.ndarray:
        assert self.center_ is not None and self.scale_ is not None
        z = np.abs(X - self.center_) / self.scale_
        return np.max(z, axis=1)

    def _state(self) -> dict[str, Any]:
        return {"center": self.center_, "scale": self.scale_, "min_scale": self.min_scale}

    def _load_state(self, state: dict[str, Any]) -> None:
        self.center_ = state["center"]
        self.scale_ = state["scale"]
        self.min_scale = state["min_scale"]
