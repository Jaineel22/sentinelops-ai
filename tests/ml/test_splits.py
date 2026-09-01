"""Chronological and held-out-fault splitting — no leakage."""

from __future__ import annotations

import pandas as pd
import pytest
from ml.features.engineering import build_features
from ml.splits import chronological_split, held_out_fault_split


def _ts(df: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(df["window_start"], utc=True)


def test_chronological_split_has_no_time_overlap(signal_frame: pd.DataFrame) -> None:
    feats = build_features(signal_frame)
    split = chronological_split(feats, val_fraction=0.2, test_fraction=0.2)

    assert _ts(split.train).max() <= _ts(split.val).min()
    assert _ts(split.val).max() <= _ts(split.test).min()
    assert len(split.train) + len(split.val) + len(split.test) == len(feats)


def test_chronological_split_is_not_random(signal_frame: pd.DataFrame) -> None:
    feats = build_features(signal_frame)
    split = chronological_split(feats)
    # test set is strictly the tail
    assert list(split.test["window_start"]) == list(feats["window_start"])[-len(split.test) :]


def test_chronological_split_rejects_bad_fractions(signal_frame: pd.DataFrame) -> None:
    feats = build_features(signal_frame)
    with pytest.raises(ValueError, match="< 1"):
        chronological_split(feats, val_fraction=0.6, test_fraction=0.6)


def test_held_out_fault_split_excludes_holdout_from_training(
    holdout_frames: tuple[pd.DataFrame, pd.DataFrame],
) -> None:
    train_src, test_src = (build_features(f) for f in holdout_frames)
    split = held_out_fault_split(
        train_src,
        test_src,
        train_faults={"latency_anomaly", "error_anomaly"},
        holdout_faults={"publish_failure", "traffic_surge"},
    )
    train_labels = set(pd.concat([split.train, split.val])["label"])
    assert "publish_failure" not in train_labels
    assert "traffic_surge" not in train_labels
    assert {"publish_failure", "traffic_surge"} & set(split.test["label"])


def test_held_out_fault_split_rejects_overlap(
    holdout_frames: tuple[pd.DataFrame, pd.DataFrame],
) -> None:
    train_src, test_src = (build_features(f) for f in holdout_frames)
    with pytest.raises(ValueError, match="overlap"):
        held_out_fault_split(
            train_src,
            test_src,
            train_faults={"latency_anomaly"},
            holdout_faults={"latency_anomaly"},
        )
