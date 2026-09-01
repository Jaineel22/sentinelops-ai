"""Turn cumulative metric scrapes into per-window operational signals.

Batch: ``ml/data/raw/sentinelops/<run>/snapshots.csv``  (from the collector)
   ->  ``ml/data/processed/sentinelops/<run>/{windows.csv,labels.csv,manifest.json}``

Streaming (Phase 3): the anomaly-detector reuses :func:`window_signals` on
consecutive live :class:`~ml.data.prometheus_parse.MetricSnapshot` pairs.

A "window" is the interval between two consecutive scrapes. In the batch path a
window spanning a scenario boundary or a counter reset (process restart) is
dropped, not silently miscounted.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from pathlib import Path

import numpy as np
import pandas as pd

from ml.config import PROCESSED_DIR, RAW_DIR
from ml.data.prometheus_parse import MetricSnapshot, estimate_quantile_from_bucket_deltas
from ml.data.schema import LABEL_COLUMNS, META_COLUMNS, SIGNAL_COLUMNS
from ml.data.validation import DataValidationError

# Fields that only ever grow while the process lives (a negative delta => restart).
_CUMULATIVE_FIELDS = (
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
)


def _safe_q(bucket_deltas: dict[float, float], q: float) -> float:
    val = estimate_quantile_from_bucket_deltas(bucket_deltas, q)
    return 0.0 if (val != val) else float(val)  # NaN -> 0 (no requests in window)


def window_signals(prev: MetricSnapshot, cur: MetricSnapshot, dt: float) -> dict[str, float] | None:
    """Per-window operational signals from two cumulative snapshots ``dt`` seconds
    apart. Returns ``None`` if the window is invalid (``dt <= 0`` or a counter
    reset happened between the scrapes)."""

    if dt <= 0:
        return None
    if any(getattr(cur, f) - getattr(prev, f) < -1e-6 for f in _CUMULATIVE_FIELDS):
        return None

    req = max(cur.http_post_count_total - prev.http_post_count_total, 0.0)
    denom = req if req > 0 else 1.0
    d_5xx = max(cur.http_post_count_5xx - prev.http_post_count_5xx, 0.0)
    d_2xx = max(cur.http_post_count_2xx - prev.http_post_count_2xx, 0.0)
    d_lat_sum = cur.http_post_latency_sum_ms - prev.http_post_latency_sum_ms

    prev_b, cur_b = prev.http_post_latency_buckets, cur.http_post_latency_buckets
    bucket_deltas = {
        le: max(cur_b.get(le, 0.0) - prev_b.get(le, 0.0), 0.0) for le in set(prev_b) | set(cur_b)
    }

    pub_ok = cur.publish_success_total - prev.publish_success_total
    pub_fail = cur.publish_failure_total - prev.publish_failure_total
    pub_attempts = max(pub_ok + pub_fail, 0.0)
    pub_denom = pub_attempts if pub_attempts > 0 else 1.0
    d_pub_lat_count = max(cur.publish_latency_count - prev.publish_latency_count, 0.0)
    d_pub_lat_sum = cur.publish_latency_sum_s - prev.publish_latency_sum_s
    d_created = max(cur.orders_created_total - prev.orders_created_total, 0.0)

    return {
        "request_rate": req / dt,
        "error_rate": d_5xx / denom,
        "success_rate": d_2xx / denom,
        "latency_mean_ms": (d_lat_sum / denom if req > 0 else 0.0),
        "latency_p50_ms": _safe_q(bucket_deltas, 0.50),
        "latency_p90_ms": _safe_q(bucket_deltas, 0.90),
        "latency_p95_ms": _safe_q(bucket_deltas, 0.95),
        "publish_rate": pub_attempts / dt,
        "publish_error_rate": max(pub_fail, 0.0) / pub_denom,
        "publish_latency_mean_ms": (
            1000.0 * d_pub_lat_sum / d_pub_lat_count if d_pub_lat_count > 0 else 0.0
        ),
        "orders_created_rate": d_created / dt,
    }


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _snapshot_from_row(row: Mapping[str, object]) -> MetricSnapshot:
    """Reconstruct a :class:`MetricSnapshot` from a flattened collector CSV row."""

    def f(key: str) -> float:
        return _as_float(row.get(key, 0.0))

    buckets: dict[float, float] = {}
    for key, value in row.items():
        if key.startswith("http_post_bucket_"):
            tag = key.removeprefix("http_post_bucket_")
            le = float("inf") if tag == "inf" else float(tag)
            buckets[le] = _as_float(value)

    return MetricSnapshot(
        http_post_count_total=f("http_post_count_total"),
        http_post_count_2xx=f("http_post_count_2xx"),
        http_post_count_4xx=f("http_post_count_4xx"),
        http_post_count_5xx=f("http_post_count_5xx"),
        http_post_latency_sum_ms=f("http_post_latency_sum_ms"),
        http_post_latency_count=f("http_post_latency_count"),
        http_post_latency_buckets=buckets,
        orders_created_total=f("orders_created_total"),
        publish_success_total=f("publish_success_total"),
        publish_failure_total=f("publish_failure_total"),
        publish_latency_sum_s=f("publish_latency_sum_s"),
        publish_latency_count=f("publish_latency_count"),
    )


def prepare_run(run_id: str, *, raw_root: Path | None = None, out_root: Path | None = None) -> Path:
    raw_dir = (raw_root or RAW_DIR / "sentinelops") / run_id
    snapshots_path = raw_dir / "snapshots.csv"
    if not snapshots_path.exists():
        raise FileNotFoundError(f"no snapshots at {snapshots_path} — run the collector first")

    raw = pd.read_csv(snapshots_path).sort_values("scrape_ts").reset_index(drop=True)
    if len(raw) < 3:
        raise DataValidationError(f"run {run_id!r} has too few snapshots ({len(raw)})")

    windows: list[dict[str, object]] = []
    for i in range(1, len(raw)):
        prev_row, cur_row = raw.iloc[i - 1].to_dict(), raw.iloc[i].to_dict()
        if prev_row["scenario"] != cur_row["scenario"]:
            continue  # segment boundary
        dt = float(cur_row["scrape_ts"] - prev_row["scrape_ts"])
        signals = window_signals(_snapshot_from_row(prev_row), _snapshot_from_row(cur_row), dt)
        if signals is None:
            continue

        windows.append(
            {
                "run_id": run_id,
                "window_start": prev_row["scrape_iso"],
                "window_end": cur_row["scrape_iso"],
                "window_seconds": round(dt, 3),
                **signals,
                "scenario": cur_row["scenario"],
                "label": cur_row["label"],
                "is_anomaly": int(cur_row["is_anomaly"]),
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
