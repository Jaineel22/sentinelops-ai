"""Inference boundary: DetectorService loads independently of training."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from ml.data.schema import SIGNAL_COLUMNS
from ml.features.engineering import build_features
from ml.inference import AnomalyResult, DetectorService
from ml.models import IsolationForestDetector
from ml.models.base import FeatureSchemaError


@pytest.fixture
def saved_model(tmp_path: Path, signal_frame: pd.DataFrame) -> Path:
    feats = build_features(signal_frame)
    n = len(feats)
    det = IsolationForestDetector(random_state=42).fit(
        feats.iloc[: int(n * 0.6)], feats.iloc[: int(n * 0.6)]["is_anomaly"], random_seed=42
    )
    det.calibrate_threshold(
        feats.iloc[int(n * 0.6) : int(n * 0.8)],
        feats.iloc[int(n * 0.6) : int(n * 0.8)]["is_anomaly"],
    )
    path = det.save(tmp_path / "iforest.joblib")
    return path


def test_score_batch_returns_results(saved_model: Path, signal_frame: pd.DataFrame) -> None:
    svc = DetectorService.load(saved_model)
    results = svc.score_batch(signal_frame)
    assert len(results) == len(signal_frame)
    assert all(isinstance(r, AnomalyResult) for r in results)
    # anomalous windows should tend to score higher
    anom = [r.score for r, a in zip(results, signal_frame["is_anomaly"], strict=True) if a]
    norm = [r.score for r, a in zip(results, signal_frame["is_anomaly"], strict=True) if not a]
    assert sum(anom) / len(anom) > sum(norm) / len(norm)


def test_score_window_streaming(saved_model: Path, signal_frame: pd.DataFrame) -> None:
    svc = DetectorService.load(saved_model)
    last: AnomalyResult | None = None
    for _, rec in signal_frame.iterrows():
        last = svc.score_window({c: rec[c] for c in SIGNAL_COLUMNS})
    assert last is not None
    assert isinstance(last.is_anomaly, bool)
    assert last.threshold >= 0.0
    parsed = json.loads(last.to_json())
    assert parsed["model_type"] == "isolation_forest"
    assert set(parsed["features"]) == set(svc._detector.feature_names)


def test_streaming_and_batch_agree_on_last_window(
    saved_model: Path, signal_frame: pd.DataFrame
) -> None:
    svc = DetectorService.load(saved_model)
    batch = svc.score_batch(signal_frame)

    svc.reset_stream()
    stream_last = None
    for _, rec in signal_frame.iterrows():
        stream_last = svc.score_window({c: rec[c] for c in SIGNAL_COLUMNS})
    assert stream_last is not None
    assert abs(stream_last.score - batch[-1].score) < 1e-9


def test_wrong_signal_schema_raises(saved_model: Path) -> None:
    svc = DetectorService.load(saved_model)
    with pytest.raises(ValueError, match="missing columns"):
        svc.score_window({"request_rate": 1.0})


def test_bad_feature_frame_raises(saved_model: Path, signal_frame: pd.DataFrame) -> None:
    svc = DetectorService.load(saved_model)
    broken = build_features(signal_frame).drop(columns=["latency_p95_ms"])
    with pytest.raises(FeatureSchemaError):
        svc._detector.score_samples(broken)
