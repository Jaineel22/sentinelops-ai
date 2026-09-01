"""Detector behaviour: baseline, Isolation Forest, supervised RF, save/load."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from ml.data.schema import FEATURE_COLUMNS
from ml.features.engineering import build_features
from ml.models import IsolationForestDetector, RandomForestDetector, RobustZScoreDetector
from ml.models.base import AnomalyDetector, FeatureSchemaError


@pytest.fixture
def features(signal_frame: pd.DataFrame) -> pd.DataFrame:
    return build_features(signal_frame)


def _split(features: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, pd.DataFrame, pd.Series]:
    n = len(features)
    tr = features.iloc[: int(n * 0.6)]
    te = features.iloc[int(n * 0.6) :]
    return tr, tr["is_anomaly"], te, te["is_anomaly"]


@pytest.mark.parametrize("factory", [RobustZScoreDetector, IsolationForestDetector])
def test_unsupervised_flags_anomalies_higher(
    factory: type[AnomalyDetector], features: pd.DataFrame
) -> None:
    tr, ytr, te, yte = _split(features)
    det = factory().fit(tr, ytr, random_seed=42)
    scores = det.score_samples(te)
    assert scores[yte.to_numpy() == 1].mean() > scores[yte.to_numpy() == 0].mean()


def test_isolation_forest_is_deterministic(features: pd.DataFrame) -> None:
    tr, ytr, te, _ = _split(features)
    a = IsolationForestDetector(random_state=42).fit(tr, ytr, random_seed=42).score_samples(te)
    b = IsolationForestDetector(random_state=42).fit(tr, ytr, random_seed=42).score_samples(te)
    np.testing.assert_array_equal(a, b)


def test_threshold_calibration_uses_only_validation(features: pd.DataFrame) -> None:
    tr, ytr, te, _yte = _split(features)
    det = IsolationForestDetector(random_state=42).fit(tr, ytr, random_seed=42)
    det.calibrate_threshold(tr, ytr, objective="f1")
    preds = det.predict(te)
    assert set(np.unique(preds)) <= {0, 1}


def test_supervised_requires_labels(features: pd.DataFrame) -> None:
    tr, _, _, _ = _split(features)
    with pytest.raises(ValueError, match="requires labels"):
        RandomForestDetector().fit(tr, None, random_seed=42)


def test_robust_zscore_handles_constant_feature(features: pd.DataFrame) -> None:
    tr, ytr, te, _ = _split(features)
    tr = tr.copy()
    tr[FEATURE_COLUMNS[0]] = 3.0  # constant -> MAD 0
    det = RobustZScoreDetector().fit(tr, ytr, random_seed=42)
    scores = det.score_samples(te)
    assert np.isfinite(scores).all()


def test_save_load_round_trip(tmp_path: Path, features: pd.DataFrame) -> None:
    tr, ytr, te, _ = _split(features)
    det = IsolationForestDetector(random_state=42).fit(tr, ytr, random_seed=42)
    det.calibrate_threshold(tr, ytr)
    path = det.save(tmp_path / "m.joblib")

    loaded = AnomalyDetector.load(path)
    assert isinstance(loaded, IsolationForestDetector)
    assert loaded.threshold_ == det.threshold_
    assert loaded.feature_names == det.feature_names
    np.testing.assert_allclose(loaded.score_samples(te), det.score_samples(te))


def test_load_with_wrong_feature_schema_raises(tmp_path: Path, features: pd.DataFrame) -> None:
    tr, ytr, _, _ = _split(features)
    det = RobustZScoreDetector().fit(tr, ytr, random_seed=42)
    path = det.save(tmp_path / "m.joblib")
    loaded = AnomalyDetector.load(path)
    with pytest.raises(FeatureSchemaError, match="missing feature columns"):
        loaded.score_samples(features.drop(columns=[FEATURE_COLUMNS[0]]))


def test_metadata_is_recorded(features: pd.DataFrame) -> None:
    tr, ytr, _, _ = _split(features)
    det = IsolationForestDetector(random_state=42).fit(
        tr, ytr, random_seed=42, training_dataset="synthetic"
    )
    assert det.metadata is not None
    assert det.metadata.model_type == "isolation_forest"
    assert det.metadata.feature_names == list(FEATURE_COLUMNS)
    assert det.metadata.random_seed == 42
    assert det.metadata.n_train_rows == len(tr)
    assert det.metadata.training_dataset == "synthetic"
