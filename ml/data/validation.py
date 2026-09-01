"""Explicit data-quality validation.

The pipeline fails loudly on bad data rather than silently training on it.
Every check raises :class:`DataValidationError` with a specific message.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd


class DataValidationError(ValueError):
    """A dataset failed a quality check."""


def require_columns(df: pd.DataFrame, columns: Iterable[str]) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise DataValidationError(f"missing required columns: {sorted(missing)}")


def require_non_empty(df: pd.DataFrame, *, min_rows: int = 1) -> None:
    if len(df) < min_rows:
        raise DataValidationError(f"expected at least {min_rows} row(s), got {len(df)}")


def check_timestamps(
    df: pd.DataFrame,
    *,
    column: str = "window_start",
    group: str | None = "run_id",
    allow_duplicates: bool = False,
) -> None:
    """Timestamps must parse, be strictly increasing within each group, and
    (by default) contain no duplicates."""

    if column not in df.columns:
        raise DataValidationError(f"timestamp column {column!r} not present")

    ts = pd.to_datetime(df[column], utc=True, errors="coerce")
    if ts.isna().any():
        bad = df.loc[ts.isna(), column].head(3).tolist()
        raise DataValidationError(f"unparseable timestamps in {column!r}: {bad}")

    groups = (
        [(None, df.index)]
        if group is None or group not in df.columns
        else list(df.groupby(group).groups.items())
    )
    for name, idx in groups:
        series = ts.loc[idx]
        if not allow_duplicates and series.duplicated().any():
            raise DataValidationError(f"duplicate timestamps in group {name!r}")
        if not series.is_monotonic_increasing:
            raise DataValidationError(f"timestamps not sorted ascending in group {name!r}")


def check_no_missing(df: pd.DataFrame, columns: Iterable[str]) -> None:
    cols = [c for c in columns if c in df.columns]
    na_counts = df[cols].isna().sum()
    offenders = na_counts[na_counts > 0]
    if not offenders.empty:
        raise DataValidationError(f"missing values: {offenders.to_dict()}")


def check_finite(df: pd.DataFrame, columns: Iterable[str]) -> None:
    cols = [c for c in columns if c in df.columns]
    numeric = df[cols].select_dtypes(include=[np.number])
    inf_mask = ~np.isfinite(numeric.to_numpy())
    if inf_mask.any():
        bad = [c for c in numeric.columns if (~np.isfinite(numeric[c])).any()]
        raise DataValidationError(f"non-finite values in columns: {bad}")


def check_ranges(df: pd.DataFrame) -> None:
    """Reject physically impossible metric values."""

    rate_cols = [
        c for c in df.columns if c.endswith("_rate") and "growth" not in c and "delta" not in c
    ]
    for col in rate_cols:
        series = df[col].dropna()
        if col.endswith(("error_rate", "success_rate")) or col in {"error_rate", "success_rate"}:
            if ((series < -1e-9) | (series > 1 + 1e-9)).any():
                raise DataValidationError(f"{col} outside [0, 1]")
        elif (series < -1e-9).any():
            raise DataValidationError(f"{col} has negative values")

    for col in [c for c in df.columns if c.startswith("latency") or "latency" in c]:
        series = df[col].dropna()
        if (series < -1e-9).any():
            raise DataValidationError(f"{col} has negative latency")


def validate_signal_frame(df: pd.DataFrame, required: Iterable[str], *, min_rows: int = 10) -> None:
    """Full gate for a prepared signal frame before feature engineering."""

    require_non_empty(df, min_rows=min_rows)
    require_columns(df, required)
    check_timestamps(df)
    check_no_missing(df, required)
    check_finite(df, [c for c in df.columns if df[c].dtype.kind in "fiu"])
    check_ranges(df)
