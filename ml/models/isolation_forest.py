"""Primary detector: Isolation Forest.

Why Isolation Forest for SentinelOps:

* **Unsupervised.** A live platform rarely has a large, clean catalogue of
  labelled incidents to train a classifier on. IF learns the shape of *normal*
  and flags points that are easy to isolate.
* **Multivariate.** It considers all operational signals jointly, so it can
  catch "this combination of latency + publish rate is unusual" that per-metric
  thresholds miss.
* **Cheap and deterministic** (with a fixed ``random_state``); fits in
  milliseconds on this dataset and scores in microseconds — usable per window
  in the live path later.

A ``StandardScaler`` is fitted on the training features first so no single
large-magnitude signal dominates the splits. The raw ``score_samples`` from
scikit-learn is *higher for normal*; we negate it so higher = more anomalous,
consistent with the other detectors.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from ml.models.base import AnomalyDetector


class IsolationForestDetector(AnomalyDetector):
    def __init__(
        self,
        *,
        feature_names: list[str] | None = None,
        n_estimators: int = 200,
        max_samples: str | int | float = "auto",
        contamination: float | str = "auto",
        random_state: int = 42,
    ) -> None:
        super().__init__(feature_names=feature_names)
        self.n_estimators = n_estimators
        self.max_samples = max_samples
        self.contamination = contamination
        self.random_state = random_state
        self._scaler: StandardScaler | None = None
        self._forest: IsolationForest | None = None

    @property
    def model_type(self) -> str:
        return "isolation_forest"

    def _hyperparameters(self) -> dict[str, Any]:
        return {
            "n_estimators": self.n_estimators,
            "max_samples": self.max_samples,
            "contamination": self.contamination,
            "random_state": self.random_state,
            "scaler": "standard",
        }

    def _fit(self, X: np.ndarray, y: np.ndarray | None) -> None:
        # Semi-supervised framing: fit on normal rows only when labels exist.
        ref = X[y == 0] if y is not None and (y == 0).any() else X
        self._scaler = StandardScaler().fit(ref)
        self._forest = IsolationForest(
            n_estimators=self.n_estimators,
            max_samples=self.max_samples,
            contamination=self.contamination,
            random_state=self.random_state,
            n_jobs=1,
        ).fit(self._scaler.transform(ref))

    def _score(self, X: np.ndarray) -> np.ndarray:
        assert self._scaler is not None and self._forest is not None
        return np.asarray(-self._forest.score_samples(self._scaler.transform(X)), dtype=float)

    def _state(self) -> dict[str, Any]:
        return {
            "scaler": self._scaler,
            "forest": self._forest,
            "params": self._hyperparameters(),
        }

    def _load_state(self, state: dict[str, Any]) -> None:
        self._scaler = state["scaler"]
        self._forest = state["forest"]
        params = state.get("params", {})
        self.n_estimators = params.get("n_estimators", self.n_estimators)
        self.random_state = params.get("random_state", self.random_state)
