"""Common detector interface + artifact (de)serialisation.

Contract for every detector:

* ``fit(X, y=None)``            — learn from a feature frame (``y`` optional;
  unsupervised detectors ignore it).
* ``score_samples(X)``          — float array, **higher = more anomalous**.
* ``predict(X)``                — int array in {0, 1} using ``threshold_``.
* ``calibrate_threshold(...)``  — choose ``threshold_`` on a validation frame.
* ``save(path)`` / ``load(path)`` — a single ``.joblib`` bundle carrying the
  fitted model, the exact feature list, and metadata.

Inference (Phase 3) only needs ``load`` + ``score_samples`` / ``predict``.
"""

from __future__ import annotations

import abc
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from ml import __version__
from ml.data.schema import FEATURE_COLUMNS


class FeatureSchemaError(ValueError):
    """Feature frame does not match what the detector was trained on."""


@dataclass
class DetectorMetadata:
    model_type: str
    feature_names: list[str]
    hyperparameters: dict[str, Any]
    random_seed: int
    training_dataset: str = ""
    training_period: str = ""
    n_train_rows: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())
    ml_version: str = __version__

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AnomalyDetector(abc.ABC):
    """Base class. Subclasses set ``self.metadata`` in ``fit``."""

    def __init__(self, *, feature_names: list[str] | None = None) -> None:
        self.feature_names: list[str] = list(feature_names or FEATURE_COLUMNS)
        self.threshold_: float = 0.0
        self.metadata: DetectorMetadata | None = None
        self._fitted = False

    # --- subclass hooks ---------------------------------------------------
    @abc.abstractmethod
    def _fit(self, X: np.ndarray, y: np.ndarray | None) -> None: ...

    @abc.abstractmethod
    def _score(self, X: np.ndarray) -> np.ndarray: ...

    @property
    @abc.abstractmethod
    def model_type(self) -> str: ...

    @abc.abstractmethod
    def _hyperparameters(self) -> dict[str, Any]: ...

    # --- shared behaviour ----------------------------------------------
    def _matrix(self, X: pd.DataFrame) -> np.ndarray:
        missing = [c for c in self.feature_names if c not in X.columns]
        if missing:
            raise FeatureSchemaError(f"missing feature columns: {missing}")
        return np.asarray(X.loc[:, self.feature_names].to_numpy(), dtype=float)

    def fit(
        self,
        X: pd.DataFrame,
        y: pd.Series | np.ndarray | None = None,
        *,
        random_seed: int,
        training_dataset: str = "",
    ) -> AnomalyDetector:
        mat = self._matrix(X)
        y_arr = None if y is None else np.asarray(y, dtype=int)
        self._fit(mat, y_arr)
        self._fitted = True
        period = ""
        if {"window_start", "window_end"} <= set(X.columns):
            period = f"{X['window_start'].min()} .. {X['window_end'].max()}"
        self.metadata = DetectorMetadata(
            model_type=self.model_type,
            feature_names=list(self.feature_names),
            hyperparameters=self._hyperparameters(),
            random_seed=random_seed,
            training_dataset=training_dataset,
            training_period=period,
            n_train_rows=len(X),
        )
        return self

    def score_samples(self, X: pd.DataFrame) -> np.ndarray:
        self._check_fitted()
        return np.asarray(self._score(self._matrix(X)), dtype=float)

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return (self.score_samples(X) > self.threshold_).astype(int)

    def calibrate_threshold(
        self,
        X_val: pd.DataFrame,
        y_val: pd.Series | np.ndarray,
        *,
        objective: str = "f1",
        target_fpr: float = 0.05,
    ) -> float:
        """Pick ``threshold_`` on validation data only.

        ``objective="f1"``    -> threshold maximising validation F1.
        ``objective="fpr"``   -> lowest threshold with validation FPR <= target.
        """

        scores = self.score_samples(X_val)
        y = np.asarray(y_val, dtype=int)
        candidates = np.unique(scores)
        if candidates.size == 0:
            self.threshold_ = 0.0
            return self.threshold_

        # midpoints + open ends so every partition is reachable
        mids = (candidates[:-1] + candidates[1:]) / 2 if candidates.size > 1 else candidates
        grid = np.concatenate([[candidates.min() - 1e-9], mids, [candidates.max() + 1e-9]])

        best_t = float(grid[-1])
        best_key = (-1.0, -1.0)
        for t in grid:
            pred = (scores > t).astype(int)
            tp = int(((pred == 1) & (y == 1)).sum())
            fp = int(((pred == 1) & (y == 0)).sum())
            fn = int(((pred == 0) & (y == 1)).sum())
            tn = int(((pred == 0) & (y == 0)).sum())
            precision = tp / (tp + fp) if tp + fp else 0.0
            recall = tp / (tp + fn) if tp + fn else 0.0
            f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
            fpr = fp / (fp + tn) if fp + tn else 0.0
            if objective == "fpr":
                # Prefer thresholds meeting the FPR budget (maximise recall among
                # them); if none do, fall back to the lowest FPR.
                key = (1.0, recall) if fpr <= target_fpr else (0.0, -fpr)
            else:
                key = (f1, -fpr)
            if key > best_key:
                best_key, best_t = key, float(t)
        self.threshold_ = best_t
        return best_t

    # --- persistence --------------------------------------------------
    def _state(self) -> dict[str, Any]:
        """Subclasses add their fitted estimator(s) here."""
        return {}

    def _load_state(self, state: dict[str, Any]) -> None:  # noqa: B027 - optional hook
        """Subclasses restore their fitted estimator(s) here."""

    def save(self, path: str | Path) -> Path:
        self._check_fitted()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        bundle = {
            "class": type(self).__name__,
            "feature_names": self.feature_names,
            "threshold": self.threshold_,
            "metadata": self.metadata.to_dict() if self.metadata else None,
            "state": self._state(),
        }
        joblib.dump(bundle, path)
        return path

    @classmethod
    def load(cls, path: str | Path) -> AnomalyDetector:
        bundle = joblib.load(Path(path))
        subclasses = _concrete_detectors()
        class_name = bundle["class"]
        if class_name not in subclasses:
            raise ValueError(
                f"unknown detector class {class_name!r} in {path} (known: {sorted(subclasses)})"
            )
        obj: AnomalyDetector = subclasses[class_name](feature_names=bundle["feature_names"])
        obj.threshold_ = float(bundle["threshold"])
        obj._load_state(bundle["state"])
        obj._fitted = True
        if bundle["metadata"]:
            obj.metadata = DetectorMetadata(**bundle["metadata"])
        return obj

    def _check_fitted(self) -> None:
        if not self._fitted:
            raise RuntimeError(f"{type(self).__name__} is not fitted")


def _concrete_detectors() -> dict[str, type[AnomalyDetector]]:
    """Name -> class for every concrete ``AnomalyDetector`` subclass."""

    seen: dict[str, type[AnomalyDetector]] = {}
    stack: list[type[AnomalyDetector]] = list(AnomalyDetector.__subclasses__())
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        if not getattr(cls, "__abstractmethods__", None):
            seen[cls.__name__] = cls
    return seen
