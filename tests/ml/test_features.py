"""Feature engineering: correctness, causality, no leakage, batch==stream."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from ml.data.schema import FEATURE_COLUMNS, FORBIDDEN_FEATURE_SUBSTRINGS, SIGNAL_COLUMNS
from ml.features.engineering import FeatureConfig, build_features
from ml.inference.featurizer import StreamFeaturizer


def test_produces_all_feature_columns(signal_frame: pd.DataFrame) -> None:
    feats = build_features(signal_frame)
    for col in FEATURE_COLUMNS:
        assert col in feats.columns
    assert not feats[list(FEATURE_COLUMNS)].isna().any().any()
    assert np.isfinite(feats[list(FEATURE_COLUMNS)].to_numpy()).all()


def test_no_forbidden_feature_columns() -> None:
    for col in FEATURE_COLUMNS:
        for bad in FORBIDDEN_FEATURE_SUBSTRINGS:
            assert bad not in col, f"feature {col!r} looks like leakage ({bad!r})"


def test_features_are_causal(signal_frame: pd.DataFrame) -> None:
    """Changing a future row must not change past feature rows."""

    feats = build_features(signal_frame)
    perturbed = signal_frame.copy()
    perturbed.loc[100, list(SIGNAL_COLUMNS)] *= 5.0
    feats2 = build_features(perturbed)

    past = feats.iloc[:98][list(FEATURE_COLUMNS)].to_numpy()
    past2 = feats2.iloc[:98][list(FEATURE_COLUMNS)].to_numpy()
    assert np.allclose(past, past2)


def test_rolling_stats_do_not_cross_runs() -> None:
    from tests.ml.conftest import _make_signal_frame

    a = _make_signal_frame(n=20, run_id="a", seed=1, anomaly_slices=[])
    b = _make_signal_frame(n=20, run_id="b", seed=2, anomaly_slices=[])
    b.loc[:, "request_rate"] = 999.0  # b is wildly different
    combined = pd.concat([a, b], ignore_index=True)

    feats = build_features(combined)
    a_only = build_features(a)
    # run a's engineered features are identical whether or not run b is present
    assert np.allclose(
        feats[feats["run_id"] == "a"][list(FEATURE_COLUMNS)].to_numpy(),
        a_only[list(FEATURE_COLUMNS)].to_numpy(),
    )


def test_deterministic(signal_frame: pd.DataFrame) -> None:
    a = build_features(signal_frame)
    b = build_features(signal_frame.copy())
    pd.testing.assert_frame_equal(a, b)


def test_constant_signal_gives_zero_rolling_std() -> None:
    from tests.ml.conftest import _make_signal_frame

    df = _make_signal_frame(n=15, run_id="c", seed=1, anomaly_slices=[])
    df["request_rate"] = 5.0
    feats = build_features(df)
    assert (feats["request_rate_roll_std"] == 0.0).all()
    assert (feats["request_rate_delta"] == 0.0).all()


def test_streaming_matches_batch(signal_frame: pd.DataFrame) -> None:
    cfg = FeatureConfig()
    batch = build_features(signal_frame, cfg)

    featurizer = StreamFeaturizer(cfg)
    stream_rows = []
    for _, rec in signal_frame.iterrows():
        stream_rows.append(featurizer.push(rec.to_dict()))
    stream = pd.concat(stream_rows, ignore_index=True)

    # Once the buffer is full (index >= rolling_window - 1) the two must agree.
    warm = cfg.rolling_window - 1
    np.testing.assert_allclose(
        batch.iloc[warm:][list(FEATURE_COLUMNS)].to_numpy(),
        stream.iloc[warm:][list(FEATURE_COLUMNS)].to_numpy(),
        rtol=1e-9,
        atol=1e-9,
    )


def test_missing_signal_column_raises(signal_frame: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="missing signal columns"):
        build_features(signal_frame.drop(columns=["publish_rate"]))
