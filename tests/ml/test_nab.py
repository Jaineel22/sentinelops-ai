"""Track B (NAB) loading + feature building.

These tests need the downloaded NAB data (`make nab-download`); they skip
cleanly when it is absent, so CI without network still passes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from ml.data.nab import (
    ALL_SERIES,
    NAB_FEATURE_COLUMNS,
    build_nab_features,
    load_nab_series,
    verify_against_manifest,
)

_HAVE_NAB = verify_against_manifest()
pytestmark = pytest.mark.skipif(not _HAVE_NAB, reason="NAB data not downloaded")


@pytest.mark.parametrize("rel", ALL_SERIES)
def test_series_loads_with_binary_labels(rel: str) -> None:
    df = load_nab_series(rel)
    assert {"timestamp", "value", "is_anomaly"} <= set(df.columns)
    assert df["timestamp"].is_monotonic_increasing
    assert set(df["is_anomaly"].unique()) <= {0, 1}
    assert 0 < df["is_anomaly"].sum() < len(df)  # some, not all


@pytest.mark.parametrize("rel", ALL_SERIES)
def test_features_are_finite_and_causal(rel: str) -> None:
    feats = build_nab_features(load_nab_series(rel))
    x = feats[list(NAB_FEATURE_COLUMNS)].to_numpy()
    assert np.isfinite(x).all()
    assert len(feats) == len(load_nab_series(rel))

    # causality: perturbing the last row must not change earlier feature rows
    df = load_nab_series(rel)
    perturbed = df.copy()
    perturbed.loc[perturbed.index[-1], "value"] *= 3.0
    feats2 = build_nab_features(perturbed)
    n = len(feats) - 2
    np.testing.assert_allclose(
        feats.iloc[:n][list(NAB_FEATURE_COLUMNS)].to_numpy(),
        feats2.iloc[:n][list(NAB_FEATURE_COLUMNS)].to_numpy(),
    )


def test_manifest_checksums_hold() -> None:
    assert verify_against_manifest() is True


def test_missing_series_raises(tmp_path: object) -> None:
    from pathlib import Path

    with pytest.raises(FileNotFoundError):
        load_nab_series("realKnownCause/does_not_exist.csv", dest=Path(str(tmp_path)))


def test_build_nab_features_handles_short_series() -> None:
    ts = pd.date_range("2026-01-01", periods=20, freq="5min")
    df = pd.DataFrame(
        {"timestamp": ts, "value": np.arange(20.0), "is_anomaly": [0] * 18 + [1, 1], "series": "s"}
    )
    feats = build_nab_features(df)
    assert len(feats) == 20
    assert np.isfinite(feats[list(NAB_FEATURE_COLUMNS)].to_numpy()).all()
