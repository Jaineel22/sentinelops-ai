"""Leakage-safe dataset splitting.

Two strategies, both deliberately *not* a random split:

* :func:`chronological_split` — earliest windows train, middle validate, latest
  test. Prevents temporal leakage: the model is never trained on data from
  after the data it is tested on.
* :func:`held_out_fault_split` — train on a chosen set of fault types, test on
  fault types the model has never seen. Answers "can it flag an anomaly that
  differs from the faults it was exposed to?".
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ml.collection.scenarios import NORMAL_LABELS


@dataclass(frozen=True)
class Split:
    train: pd.DataFrame
    val: pd.DataFrame
    test: pd.DataFrame

    def summary(self) -> dict[str, dict[str, int]]:
        def counts(df: pd.DataFrame) -> dict[str, int]:
            base = {"rows": len(df)}
            if "is_anomaly" in df.columns:
                base["anomaly"] = int(df["is_anomaly"].sum())
            return base

        return {"train": counts(self.train), "val": counts(self.val), "test": counts(self.test)}


def _time_key(df: pd.DataFrame) -> pd.Series:
    if "window_start" not in df.columns:
        raise ValueError("chronological split needs a 'window_start' column")
    return pd.to_datetime(df["window_start"], utc=True)


def chronological_split(
    df: pd.DataFrame, *, val_fraction: float = 0.2, test_fraction: float = 0.2
) -> Split:
    if not 0 < val_fraction < 1 or not 0 < test_fraction < 1:
        raise ValueError("fractions must be in (0, 1)")
    if val_fraction + test_fraction >= 1:
        raise ValueError("val + test fractions must be < 1")

    ordered = (
        df.assign(_t=_time_key(df)).sort_values("_t").drop(columns="_t").reset_index(drop=True)
    )
    n = len(ordered)
    n_test = max(1, round(n * test_fraction))
    n_val = max(1, round(n * val_fraction))
    n_train = n - n_val - n_test
    if n_train <= 0:
        raise ValueError(f"not enough rows ({n}) for the requested split")

    train = ordered.iloc[:n_train]
    val = ordered.iloc[n_train : n_train + n_val]
    test = ordered.iloc[n_train + n_val :]

    # Hard guarantee: no time overlap across boundaries.
    assert _time_key(train).max() <= _time_key(val).min()
    assert _time_key(val).max() <= _time_key(test).min()
    return Split(
        train.reset_index(drop=True), val.reset_index(drop=True), test.reset_index(drop=True)
    )


def held_out_fault_split(
    train_source: pd.DataFrame,
    test_source: pd.DataFrame,
    *,
    train_faults: set[str],
    holdout_faults: set[str],
    val_fraction: float = 0.25,
) -> Split:
    """Build a generalization experiment.

    ``train``/``val`` come from ``train_source`` restricted to normal windows
    plus ``train_faults``. ``test`` comes from ``test_source`` restricted to
    normal windows plus ``holdout_faults``. The function asserts the holdout
    fault labels never appear in train/val.
    """

    if train_faults & holdout_faults:
        raise ValueError("train_faults and holdout_faults overlap")

    keep_train = NORMAL_LABELS | train_faults
    keep_test = NORMAL_LABELS | holdout_faults
    tr = train_source[train_source["label"].isin(keep_train)].copy()
    te = test_source[test_source["label"].isin(keep_test)].copy()

    tr = tr.assign(_t=_time_key(tr)).sort_values("_t").drop(columns="_t").reset_index(drop=True)
    cut = int(len(tr) * (1 - val_fraction))
    train, val = tr.iloc[:cut].reset_index(drop=True), tr.iloc[cut:].reset_index(drop=True)

    leaked = set(pd.concat([train, val])["label"]) & holdout_faults
    assert not leaked, f"held-out faults leaked into training: {leaked}"
    return Split(train, val, te.reset_index(drop=True))
