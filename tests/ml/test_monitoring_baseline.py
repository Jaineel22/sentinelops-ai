"""Baseline freezing (Phase 6D) - structure, statistics, binning, round-trip."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from ml.monitoring.baseline import (
    BaselineDistribution,
    BaselineError,
    freeze_baseline,
    load_baseline,
    save_baseline,
)

_FEATURES = ["latency_ms", "error_rate", "request_rate"]


def _training_frame(n: int = 600, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "latency_ms": rng.normal(120.0, 20.0, n),
            "error_rate": rng.uniform(0.0, 0.05, n),
            "request_rate": rng.normal(6.0, 0.5, n),
        }
    )


def test_freeze_baseline_creates_distribution() -> None:
    baseline = freeze_baseline(
        _training_frame(), _FEATURES, model_version="1", feature_schema_version="1"
    )

    assert isinstance(baseline, BaselineDistribution)
    assert baseline.feature_names == _FEATURES
    assert baseline.n_samples == 600
    assert baseline.model_version == "1"
    assert baseline.feature_schema_version == "1"
    for name in _FEATURES:
        assert len(baseline.bin_edges[name]) >= 2
        # proportions are a valid distribution over the bins
        props = baseline.reference_proportions[name]
        assert len(props) == len(baseline.bin_edges[name]) - 1
        assert pytest.approx(sum(props), abs=1e-9) == 1.0


def test_freeze_baseline_uses_all_features() -> None:
    baseline = freeze_baseline(
        _training_frame(), _FEATURES, model_version="1", feature_schema_version="1"
    )
    assert set(baseline.bin_edges) == set(_FEATURES)
    assert set(baseline.reference_proportions) == set(_FEATURES)
    assert set(baseline.statistics) == set(_FEATURES)


def test_baseline_statistics_correct() -> None:
    frame = _training_frame()
    baseline = freeze_baseline(frame, _FEATURES, model_version="1", feature_schema_version="1")
    stats = baseline.statistics["latency_ms"]
    col = frame["latency_ms"].to_numpy()

    assert stats["mean"] == pytest.approx(float(np.mean(col)))
    assert stats["std"] == pytest.approx(float(np.std(col)))
    assert stats["min"] == pytest.approx(float(np.min(col)))
    assert stats["max"] == pytest.approx(float(np.max(col)))
    assert stats["p50"] == pytest.approx(float(np.quantile(col, 0.5)))


def test_baseline_bin_edges_are_sorted_quantiles() -> None:
    frame = _training_frame()
    baseline = freeze_baseline(
        frame, _FEATURES, model_version="1", feature_schema_version="1", n_bins=10
    )
    edges = baseline.bin_edges["request_rate"]

    assert edges == sorted(edges)
    assert len(edges) == 11  # 10 bins
    col = frame["request_rate"].to_numpy()
    assert edges[0] == pytest.approx(float(np.min(col)))
    assert edges[-1] == pytest.approx(float(np.max(col)))
    # reference proportions are ~1/n_bins each (quantile binning)
    for p in baseline.reference_proportions["request_rate"]:
        assert 0.05 < p < 0.20


def test_constant_feature_collapses_to_one_bin() -> None:
    frame = _training_frame()
    frame["error_rate"] = 0.01  # constant
    baseline = freeze_baseline(frame, _FEATURES, model_version="1", feature_schema_version="1")
    assert baseline.reference_proportions["error_rate"] == [1.0]


def test_baseline_save_load_roundtrip(tmp_path: Path) -> None:
    baseline = freeze_baseline(
        _training_frame(), _FEATURES, model_version="3", feature_schema_version="1"
    )
    path = save_baseline(baseline, tmp_path / "b.joblib")
    loaded = load_baseline(path)

    assert loaded.to_dict() == baseline.to_dict()
    assert loaded.model_version == "3"


def test_baseline_rejects_empty_input() -> None:
    with pytest.raises(BaselineError):
        freeze_baseline(pd.DataFrame({c: [] for c in _FEATURES}), _FEATURES, "1", "1")
    with pytest.raises(BaselineError):
        freeze_baseline(_training_frame(), [], "1", "1")


def test_baseline_rejects_mismatched_features() -> None:
    with pytest.raises(BaselineError, match="missing columns"):
        freeze_baseline(_training_frame(), [*_FEATURES, "not_a_column"], "1", "1")


def test_baseline_accepts_ndarray_with_names() -> None:
    arr = _training_frame().to_numpy()
    baseline = freeze_baseline(arr, _FEATURES, model_version="1", feature_schema_version="1")
    assert baseline.n_samples == 600
    with pytest.raises(BaselineError, match="does not match"):
        freeze_baseline(arr, _FEATURES[:2], "1", "1")
