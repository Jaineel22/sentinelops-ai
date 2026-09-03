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
from typing import TYPE_CHECKING, Any

import pandas as pd

from ml.features.engineering import FeatureConfig
from ml.inference.featurizer import StreamFeaturizer
from ml.models.base import AnomalyDetector

if TYPE_CHECKING:
    from ml.mlops.config import MLflowSettings


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
    def __init__(
        self,
        detector: AnomalyDetector,
        *,
        feature_config: FeatureConfig | None = None,
        model_version: str | None = None,
        source: str = "local",
    ):
        self._detector = detector
        self._featurizer = StreamFeaturizer(feature_config)
        # `model_version` is an explicit override (the MLflow registry version
        # number when loaded via `from_registry`); otherwise the model's own
        # metadata version is used. `source` records provenance for `/ready`.
        self._model_version = model_version
        self._source = source
        self._source_details: dict[str, str] = {}

    @classmethod
    def load(
        cls,
        model_path: str | Path,
        *,
        feature_config: FeatureConfig | None = None,
        model_version: str | None = None,
        source: str = "local",
    ) -> DetectorService:
        return cls(
            AnomalyDetector.load(model_path),
            feature_config=feature_config,
            model_version=model_version,
            source=source,
        )

    @classmethod
    def from_registry(
        cls, settings: MLflowSettings, *, feature_config: FeatureConfig | None = None
    ) -> DetectorService:
        """Resolve ``settings.model_alias`` in the MLflow registry, download that
        model version's bundle, and load it with the existing ``AnomalyDetector``
        loader (no ``mlflow.pyfunc`` / ``mlflow.sklearn`` — the artifact is always
        a bundle produced by this project's training pipeline).

        Raises if ``mlflow`` is not installed, the registry is unreachable, or the
        alias is unset. Callers own the fail-safe policy.
        """

        from mlflow.artifacts import download_artifacts

        from ml.mlops.registry import resolve_alias

        version, run_id, model_uri = resolve_alias(settings)
        downloaded = Path(download_artifacts(artifact_uri=model_uri))
        bundle = downloaded if downloaded.is_file() else _first_bundle(downloaded)

        service = cls(
            AnomalyDetector.load(bundle),
            feature_config=feature_config,
            model_version=str(version),
            source="registry",
        )
        service._source_details = {
            "tracking_uri": settings.tracking_uri,
            "model_name": settings.registered_model_name,
            "alias": settings.model_alias,
            "version": str(version),
            "run_id": str(run_id),
        }
        return service

    @property
    def model_type(self) -> str:
        return self._detector.metadata.model_type if self._detector.metadata else "unknown"

    @property
    def model_version(self) -> str:
        if self._model_version is not None:
            return self._model_version
        return self._detector.metadata.ml_version if self._detector.metadata else "unknown"

    @property
    def source(self) -> str:
        """Provenance: ``"local"``, ``"registry"``, or ``"local-fallback"``."""
        return self._source

    @property
    def source_details(self) -> dict[str, str]:
        return dict(self._source_details)

    def _mark_source(self, source: str) -> None:
        """Re-label provenance (used when a registry load falls back to local)."""
        self._source = source

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


def _first_bundle(directory: Path) -> Path:
    """The single ``*.joblib`` inside a downloaded model-version artifact tree."""

    bundles = sorted(directory.rglob("*.joblib"))
    if not bundles:
        raise FileNotFoundError(f"no .joblib model bundle under {directory}")
    return bundles[0]
