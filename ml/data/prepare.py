"""Turn raw cumulative metric scrapes into per-window operational signals.

Input : ``ml/data/raw/sentinelops/<run>/snapshots.csv``  (from the collector)
Output: ``ml/data/processed/sentinelops/<run>/{windows.csv,labels.csv,manifest.json}``

A "window" is the interval between two consecutive scrapes *within the same
scenario segment*. Boundary windows (scenario changed) and windows spanning a
counter reset (process restart) are dropped, not silently miscounted.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from ml.config import PROCESSED_DIR, RAW_DIR
from ml.data.prometheus_parse import estimate_quantile_from_bucket_deltas
from ml.data.schema import LABEL_COLUMNS, META_COLUMNS, SIGNAL_COLUMNS
from ml.data.validation import DataValidationError

_CUMULATIVE_COLS = [
    "http_post_count_total",
    "http_post_count_2xx",
    "http_post_count_4xx",
    "http_post_count_5xx",
    "http_post_latency_sum_ms",
    "orders_created_total",
    "publish_success_total",
    "publish_failure_total",
    "publish_latency_sum_s",
    "publish_latency_count",
]


def _bucket_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c.startswith("http_post_bucket_")]


def _le_of(col: str) -> float:
    tag = col.removeprefix("http_post_bucket_")
    return float("inf") if tag == "inf" else float(tag)


def prepare_run(run_id: str, *, raw_root: Path | None = None, out_root: Path | None = None) -> Path:
    raw_dir = (raw_root or RAW_DIR / "sentinelops") / run_id
    snapshots_path = raw_dir / "snapshots.csv"
    if not snapshots_path.exists():
        raise FileNotFoundError(f"no snapshots at {snapshots_path} — run the collector first")

    raw = pd.read_csv(snapshots_path).sort_values("scrape_ts").reset_index(drop=True)
    if len(raw) < 3:
        raise DataValidationError(f"run {run_id!r} has too few snapshots ({len(raw)})")

    bucket_cols = _bucket_columns(raw)
    windows: list[dict[str, object]] = []

    for i in range(1, len(raw)):
        prev, cur = raw.iloc[i - 1], raw.iloc[i]
        if prev["scenario"] != cur["scenario"]:
            continue  # segment boundary
        dt = float(cur["scrape_ts"] - prev["scrape_ts"])
        if dt <= 0:
            continue

        deltas = {c: float(cur[c] - prev[c]) for c in _CUMULATIVE_COLS if c in raw.columns}
        if any(v < -1e-6 for v in deltas.values()):
            continue  # counter reset between scrapes

        req = max(deltas["http_post_count_total"], 0.0)
        denom = req if req > 0 else 1.0
        bucket_deltas = {_le_of(c): max(float(cur[c] - prev[c]), 0.0) for c in bucket_cols}
        pub_attempts = max(deltas["publish_success_total"] + deltas["publish_failure_total"], 0.0)
        pub_denom = pub_attempts if pub_attempts > 0 else 1.0
        pub_lat_count = max(deltas["publish_latency_count"], 0.0)

        windows.append(
            {
                "run_id": run_id,
                "window_start": prev["scrape_iso"],
                "window_end": cur["scrape_iso"],
                "window_seconds": round(dt, 3),
                "request_rate": req / dt,
                "error_rate": max(deltas["http_post_count_5xx"], 0.0) / denom,
                "success_rate": max(deltas["http_post_count_2xx"], 0.0) / denom,
                "latency_mean_ms": (deltas["http_post_latency_sum_ms"] / denom if req > 0 else 0.0),
                "latency_p50_ms": _safe_q(bucket_deltas, 0.50),
                "latency_p90_ms": _safe_q(bucket_deltas, 0.90),
                "latency_p95_ms": _safe_q(bucket_deltas, 0.95),
                "publish_rate": pub_attempts / dt,
                "publish_error_rate": max(deltas["publish_failure_total"], 0.0) / pub_denom,
                "publish_latency_mean_ms": (
                    1000.0 * deltas["publish_latency_sum_s"] / pub_lat_count
                    if pub_lat_count > 0
                    else 0.0
                ),
                "orders_created_rate": max(deltas["orders_created_total"], 0.0) / dt,
                "scenario": cur["scenario"],
                "label": cur["label"],
                "is_anomaly": int(cur["is_anomaly"]),
            }
        )

    if not windows:
        raise DataValidationError(f"run {run_id!r} produced no valid windows")

    frame = pd.DataFrame(windows)
    frame[list(SIGNAL_COLUMNS)] = frame[list(SIGNAL_COLUMNS)].replace([np.inf, -np.inf], np.nan)
    frame = frame.dropna(subset=list(SIGNAL_COLUMNS)).reset_index(drop=True)

    out_dir = (out_root or PROCESSED_DIR / "sentinelops") / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    windows_df = frame[list(META_COLUMNS) + list(SIGNAL_COLUMNS)].copy()
    labels_df = frame[[*list(META_COLUMNS), "scenario", *LABEL_COLUMNS]].copy()
    windows_df.to_csv(out_dir / "windows.csv", index=False)
    labels_df.to_csv(out_dir / "labels.csv", index=False)

    src_manifest = raw_dir / "manifest.json"
    manifest = json.loads(src_manifest.read_text()) if src_manifest.exists() else {"run_id": run_id}
    manifest["n_windows"] = len(windows_df)
    manifest["label_counts"] = labels_df["label"].value_counts().to_dict()
    manifest["anomaly_fraction"] = round(float(labels_df["is_anomaly"].mean()), 4)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if src_manifest.exists():
        shutil.copy(src_manifest, out_dir / "raw_manifest.json")

    print(
        f"{run_id}: {len(windows_df)} windows, "
        f"anomaly fraction {manifest['anomaly_fraction']}, "
        f"labels {manifest['label_counts']}"
    )
    return out_dir


def _safe_q(bucket_deltas: dict[float, float], q: float) -> float:
    val = estimate_quantile_from_bucket_deltas(bucket_deltas, q)
    return 0.0 if (val != val) else float(val)  # NaN -> 0 (no requests in window)


def load_processed_run(run_id: str, *, root: Path | None = None) -> pd.DataFrame:
    """Load a prepared run as one frame (signals + labels joined)."""

    base = (root or PROCESSED_DIR / "sentinelops") / run_id
    windows = pd.read_csv(base / "windows.csv")
    labels = pd.read_csv(base / "labels.csv")
    return windows.merge(labels, on=list(META_COLUMNS), how="inner")


if __name__ == "__main__":
    import sys

    for rid in sys.argv[1:] or ["run_a", "run_b"]:
        prepare_run(rid)
