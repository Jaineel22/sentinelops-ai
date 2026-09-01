"""Feature engineering for Track A telemetry.

``build_features`` is the single source of truth: training calls it on a whole
frame, inference calls it on a short trailing buffer ending at the window to
score. ``tests/ml/test_features.py`` asserts the two paths agree row-for-row.

All engineered features are **causal**: a feature for window *t* uses only
windows ``<= t`` (trailing rolling windows, backward differences, lag-1). This
is what makes the chronological train/val/test split leak-free.

Rolling / lag features are computed **per run** (``run_id``) so statistics never
bleed across independent collection runs.

For every feature, see the table in docs/architecture/phase-2.md:
what it represents, why it matters operationally, how it is computed, whether it
is available at real-time inference, and its leakage risk.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ml.data.schema import FEATURE_COLUMNS, META_COLUMNS, SIGNAL_COLUMNS

_EPS = 1.0  # request_rate floor for growth-rate denominator (requests/second)


@dataclass(frozen=True)
class FeatureConfig:
    rolling_window: int = 3  # windows (current + 2 prior) for rolling stats

    def __post_init__(self) -> None:
        if self.rolling_window < 2:
            raise ValueError("rolling_window must be >= 2")


def _engineer_group(g: pd.DataFrame, cfg: FeatureConfig) -> pd.DataFrame:
    k = cfg.rolling_window
    out = g.copy()

    def roll_mean(col: str) -> pd.Series:
        return g[col].rolling(k, min_periods=1).mean()

    def roll_std(col: str) -> pd.Series:
        return g[col].rolling(k, min_periods=2).std().fillna(0.0)

    def delta(col: str) -> pd.Series:
        return g[col].diff().fillna(0.0)

    out["request_rate_roll_mean"] = roll_mean("request_rate")
    out["request_rate_roll_std"] = roll_std("request_rate")
    out["request_rate_delta"] = delta("request_rate")
    out["error_rate_roll_mean"] = roll_mean("error_rate")
    out["error_rate_delta"] = delta("error_rate")
    out["latency_mean_ms_roll_mean"] = roll_mean("latency_mean_ms")
    out["latency_mean_ms_roll_std"] = roll_std("latency_mean_ms")
    out["latency_mean_ms_delta"] = delta("latency_mean_ms")
    out["latency_p95_ms_roll_mean"] = roll_mean("latency_p95_ms")
    out["publish_error_rate_roll_mean"] = roll_mean("publish_error_rate")
    out["publish_latency_mean_ms_roll_mean"] = roll_mean("publish_latency_mean_ms")

    prev_rate = g["request_rate"].shift(1)
    out["traffic_growth_rate"] = (
        (g["request_rate"] - prev_rate) / prev_rate.clip(lower=_EPS)
    ).fillna(0.0)

    return out


def build_features(df: pd.DataFrame, config: FeatureConfig | None = None) -> pd.DataFrame:
    """Return a frame with ``META_COLUMNS`` + ``FEATURE_COLUMNS`` (+ label
    columns passed through untouched if present)."""

    cfg = config or FeatureConfig()
    missing = [c for c in SIGNAL_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"cannot build features, missing signal columns: {missing}")

    sort_cols = [c for c in ("run_id", "window_start") if c in df.columns]
    ordered = df.sort_values(sort_cols) if sort_cols else df.copy()

    if "run_id" in ordered.columns:
        parts: list[pd.DataFrame] = []
        for run_id, group in ordered.groupby("run_id", sort=False):
            engineered_group = _engineer_group(group, cfg)
            engineered_group["run_id"] = run_id
            parts.append(engineered_group)
        engineered = pd.concat(parts, ignore_index=True)
    else:
        engineered = _engineer_group(ordered, cfg).reset_index(drop=True)

    engineered = engineered.replace([np.inf, -np.inf], np.nan)
    engineered[list(FEATURE_COLUMNS)] = engineered[list(FEATURE_COLUMNS)].fillna(0.0)

    passthrough = [c for c in ("label", "is_anomaly", "scenario") if c in engineered.columns]
    meta = [c for c in META_COLUMNS if c in engineered.columns]
    return engineered[[*meta, *FEATURE_COLUMNS, *passthrough]].reset_index(drop=True)
