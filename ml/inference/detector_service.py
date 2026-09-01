"""``DetectorService`` — load a trained detector and score telemetry windows.

This is the object Phase 3 imports. It owns nothing stateful beyond the loaded
model and a streaming featurizer; no Kafka, no HTTP, no persistence.

    svc = DetectorService.load("artifacts/models/isolation_forest_sentinelops.joblib")
    result = svc.score_window(signal_record)   # -> AnomalyResult
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from ml.features.engineering import FeatureConfig
from ml.inference.featurizer import StreamFeaturizer
from ml.models.base import AnomalyDetector


@dataclass(frozen=True)
class AnomalyResult:
    window_start: str
    window_end: str
    score: float
    threshold: float
    is_anomaly: bool
    model_type: str
    model_version: str
    features: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


class DetectorService:
    def __init__(self, detector: AnomalyDetector, *, feature_config: FeatureConfig | None = None):
        self._detector = detector
        self._featurizer = StreamFeaturizer(feature_config)

    @classmethod
    def load(
        cls, model_path: str | Path, *, feature_config: FeatureConfig | None = None
    ) -> DetectorService:
        return cls(AnomalyDetector.load(model_path), feature_config=feature_config)

    @property
    def model_type(self) -> str:
        return self._detector.metadata.model_type if self._detector.metadata else "unknown"

    @property
    def model_version(self) -> str:
        return self._detector.metadata.ml_version if self._detector.metadata else "unknown"

    def reset_stream(self) -> None:
        self._featurizer.reset()

    def score_window(self, signal_record: Mapping[str, Any]) -> AnomalyResult:
        """Score one telemetry window (streaming: uses the trailing buffer)."""

        features = self._featurizer.push(signal_record)
        return self._result_from_features(features)

    def score_batch(self, signals: pd.DataFrame) -> list[AnomalyResult]:
        """Score a batch of windows. Features are built over the whole frame
        (per ``run_id`` if present), matching training exactly."""

        from ml.features.engineering import build_features

        features = build_features(signals, self._featurizer.config)
        scores = self._detector.score_samples(features)
        preds = scores > self._detector.threshold_
        results: list[AnomalyResult] = []
        for i in range(len(features)):
            results.append(self._make_result(features.iloc[[i]], float(scores[i]), bool(preds[i])))
        return results

    # --- internals ---------------------------------------------------
    def _result_from_features(self, features: pd.DataFrame) -> AnomalyResult:
        score = float(self._detector.score_samples(features)[0])
        return self._make_result(features, score, score > self._detector.threshold_)

    def _make_result(self, row: pd.DataFrame, score: float, is_anomaly: bool) -> AnomalyResult:
        r = row.iloc[0]
        feat_cols: Sequence[str] = self._detector.feature_names
        return AnomalyResult(
            window_start=str(r.get("window_start", "")),
            window_end=str(r.get("window_end", "")),
            score=round(score, 6),
            threshold=round(float(self._detector.threshold_), 6),
            is_anomaly=bool(is_anomaly),
            model_type=self.model_type,
            model_version=self.model_version,
            features={c: round(float(r[c]), 6) for c in feat_cols},
        )
