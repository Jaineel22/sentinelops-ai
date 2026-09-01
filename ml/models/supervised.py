"""Supervised comparator: Random Forest classifier.

Included **only** to make one experimental point (Experiment 3 / 4): when
labelled fault windows are available, a supervised model beats the unsupervised
detectors on the fault types it was trained on — and then does markedly worse on
a *held-out* fault type it never saw. That contrast is the argument for shipping
the unsupervised Isolation Forest as the primary live detector.

Random Forest (not XGBoost) keeps the dependency footprint to scikit-learn,
which is enough for this comparison. XGBoost/LightGBM is deferred.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.ensemble import RandomForestClassifier

from ml.models.base import AnomalyDetector


class RandomForestDetector(AnomalyDetector):
    def __init__(
        self,
        *,
        feature_names: list[str] | None = None,
        n_estimators: int = 300,
        max_depth: int | None = 8,
        class_weight: str | None = "balanced",
        random_state: int = 42,
    ) -> None:
        super().__init__(feature_names=feature_names)
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.class_weight = class_weight
        self.random_state = random_state
        self._clf: RandomForestClassifier | None = None

    @property
    def model_type(self) -> str:
        return "random_forest_supervised"

    def _hyperparameters(self) -> dict[str, Any]:
        return {
            "n_estimators": self.n_estimators,
            "max_depth": self.max_depth,
            "class_weight": self.class_weight,
            "random_state": self.random_state,
        }

    def _fit(self, X: np.ndarray, y: np.ndarray | None) -> None:
        if y is None:
            raise ValueError("RandomForestDetector requires labels (y)")
        if len(np.unique(y)) < 2:
            raise ValueError("RandomForestDetector needs both classes present in training data")
        self._clf = RandomForestClassifier(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            class_weight=self.class_weight,
            random_state=self.random_state,
            n_jobs=1,
        ).fit(X, y)

    def _score(self, X: np.ndarray) -> np.ndarray:
        assert self._clf is not None
        return np.asarray(self._clf.predict_proba(X)[:, 1], dtype=float)

    def _state(self) -> dict[str, Any]:
        return {"clf": self._clf, "params": self._hyperparameters()}

    def _load_state(self, state: dict[str, Any]) -> None:
        self._clf = state["clf"]
