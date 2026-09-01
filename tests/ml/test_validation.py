"""Data-quality validation."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from ml.data.schema import REQUIRED_SIGNAL_COLUMNS
from ml.data.validation import (
    DataValidationError,
    check_finite,
    check_ranges,
    check_timestamps,
    require_columns,
    validate_signal_frame,
)


def test_valid_frame_passes(signal_frame: pd.DataFrame) -> None:
    validate_signal_frame(signal_frame, REQUIRED_SIGNAL_COLUMNS, min_rows=10)


def test_missing_column_raises(signal_frame: pd.DataFrame) -> None:
    with pytest.raises(DataValidationError, match="missing required columns"):
        require_columns(signal_frame.drop(columns=["request_rate"]), ["request_rate"])


def test_empty_frame_raises(signal_frame: pd.DataFrame) -> None:
    with pytest.raises(DataValidationError):
        validate_signal_frame(signal_frame.iloc[0:0], REQUIRED_SIGNAL_COLUMNS)


def test_duplicate_timestamps_raise(signal_frame: pd.DataFrame) -> None:
    dup = pd.concat([signal_frame.iloc[[5]], signal_frame], ignore_index=True)
    dup = dup.sort_values("window_start").reset_index(drop=True)
    with pytest.raises(DataValidationError, match="duplicate timestamps"):
        check_timestamps(dup)


def test_unsorted_timestamps_raise(signal_frame: pd.DataFrame) -> None:
    shuffled = signal_frame.iloc[::-1].reset_index(drop=True)
    with pytest.raises(DataValidationError, match="not sorted"):
        check_timestamps(shuffled)


def test_unparseable_timestamp_raises(signal_frame: pd.DataFrame) -> None:
    bad = signal_frame.copy()
    bad.loc[3, "window_start"] = "not-a-date"
    with pytest.raises(DataValidationError, match="unparseable"):
        check_timestamps(bad)


def test_infinite_values_raise(signal_frame: pd.DataFrame) -> None:
    bad = signal_frame.copy()
    bad.loc[10, "latency_mean_ms"] = np.inf
    with pytest.raises(DataValidationError, match="non-finite"):
        check_finite(bad, ["latency_mean_ms"])


def test_impossible_ranges_raise(signal_frame: pd.DataFrame) -> None:
    bad = signal_frame.copy()
    bad.loc[2, "error_rate"] = 1.5
    with pytest.raises(DataValidationError, match=r"error_rate"):
        check_ranges(bad)

    bad2 = signal_frame.copy()
    bad2.loc[2, "request_rate"] = -1.0
    with pytest.raises(DataValidationError, match="negative"):
        check_ranges(bad2)


def test_nan_values_raise(signal_frame: pd.DataFrame) -> None:
    bad = signal_frame.copy()
    bad.loc[7, "request_rate"] = np.nan
    with pytest.raises(DataValidationError, match="missing values"):
        validate_signal_frame(bad, REQUIRED_SIGNAL_COLUMNS)
