"""Track B: the Numenta Anomaly Benchmark (NAB).

Purpose (ADR-004 / ADR-013): an **independent** check that the *methodology*
(robust z-score baseline, Isolation Forest on engineered rolling features,
chronological split, windowed evaluation) works on real-world anomaly data that
has nothing to do with our fault generator. It is **not** used to train a model
that then scores SentinelOps telemetry — the feature spaces are unrelated.

Source : https://github.com/numenta/NAB  (Apache-licensed *code*; the data
         directory is under the repo's AGPL-3.0 license). We therefore **do not
         commit NAB data** — it is downloaded on demand and content-pinned by
         sha256 in ``ml/data/nab_manifest.json``.

    python -m ml.data.nab download
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

from ml.config import ML_DIR, NAB_RAW_DIR

NAB_REF = "master"
_RAW_BASE = f"https://raw.githubusercontent.com/numenta/NAB/{NAB_REF}"
_DATA_PREFIX = "data"  # NAB series CSVs live under data/<family>/<series>.csv
_MANIFEST_PATH = ML_DIR / "data" / "nab_manifest.json"

# Series grouped into two independent "families" for experiments 5 and 6.
NAB_FAMILIES: dict[str, list[str]] = {
    "realKnownCause": [
        "realKnownCause/machine_temperature_system_failure.csv",
        "realKnownCause/ambient_temperature_system_failure.csv",
        "realKnownCause/nyc_taxi.csv",
    ],
    "realAWSCloudwatch": [
        "realAWSCloudwatch/ec2_cpu_utilization_5f5533.csv",
        "realAWSCloudwatch/ec2_cpu_utilization_fe7f93.csv",
    ],
}
ALL_SERIES: list[str] = [s for group in NAB_FAMILIES.values() for s in group]
_LABELS_REL = "labels/combined_windows.json"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _remote_path(local_rel: str) -> str:
    """Local relative path -> path inside the NAB repo (series live under data/)."""

    return local_rel if local_rel.startswith("labels/") else f"{_DATA_PREFIX}/{local_rel}"


def _fetch(remote_path: str) -> bytes:
    url = f"{_RAW_BASE}/{remote_path}"
    with urllib.request.urlopen(url, timeout=60) as resp:
        return bytes(resp.read())


def download_nab(*, dest: Path | None = None, force: bool = False) -> Path:
    """Download the pinned NAB series + label file into ``dest`` (git-ignored)
    and (re)write the checksum manifest."""

    dest = dest or NAB_RAW_DIR
    dest.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {"ref": NAB_REF, "base": _RAW_BASE, "files": {}}
    files: dict[str, str] = {}

    for rel in [*ALL_SERIES, _LABELS_REL]:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not force:
            data = target.read_bytes()
        else:
            data = _fetch(_remote_path(rel))
            target.write_bytes(data)
        files[rel] = _sha256(data)

    manifest["files"] = files
    _MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"NAB: {len(files)} files -> {dest}\nmanifest -> {_MANIFEST_PATH}")
    return dest


def verify_against_manifest(*, dest: Path | None = None) -> bool:
    dest = dest or NAB_RAW_DIR
    if not _MANIFEST_PATH.exists():
        return False
    manifest = json.loads(_MANIFEST_PATH.read_text())
    for rel, expected in manifest["files"].items():
        path = dest / rel
        if not path.exists() or _sha256(path.read_bytes()) != expected:
            return False
    return True


def load_nab_series(rel_path: str, *, dest: Path | None = None) -> pd.DataFrame:
    """Load one NAB CSV with a binary ``is_anomaly`` column from
    ``combined_windows.json`` (1 inside a labelled anomaly window)."""

    dest = dest or NAB_RAW_DIR
    csv_path = dest / rel_path
    if not csv_path.exists():
        raise FileNotFoundError(f"{csv_path} missing — run `python -m ml.data.nab download`")

    df = pd.read_csv(csv_path, parse_dates=["timestamp"]).sort_values("timestamp")
    df = df.reset_index(drop=True)

    windows = json.loads((dest / _LABELS_REL).read_text()).get(rel_path, [])
    is_anom = np.zeros(len(df), dtype=int)
    for start, end in windows:
        mask = (df["timestamp"] >= pd.Timestamp(start)) & (df["timestamp"] <= pd.Timestamp(end))
        is_anom[mask.to_numpy()] = 1

    df["is_anomaly"] = is_anom
    df["series"] = rel_path
    return df


def build_nab_features(
    df: pd.DataFrame, *, short_window: int = 12, long_window: int = 288
) -> pd.DataFrame:
    """Univariate rolling features - the NAB analogue of
    :func:`ml.features.build_features`. Causal (trailing windows only).

    ``short_window`` (~1 h at 5-min sampling) tracks the local level; the
    ``long_window`` (~1 day) baseline gives a coarse "is this far from the
    usual daily level" signal for the seasonal series (nyc_taxi, temperatures).
    Genuine seasonal decomposition is out of scope for this methodology check.
    """

    g = df.sort_values("timestamp").reset_index(drop=True).copy()
    v = g["value"]
    lw = max(short_window + 1, min(long_window, len(g) // 3 or short_window + 1))

    roll_mean = v.rolling(short_window, min_periods=1).mean()
    roll_std = v.rolling(short_window, min_periods=2).std().fillna(0.0)
    roll_med = v.rolling(short_window, min_periods=1).median()
    roll_min = v.rolling(short_window, min_periods=1).min()
    roll_max = v.rolling(short_window, min_periods=1).max()
    long_med = v.rolling(lw, min_periods=1).median()
    long_iqr = (
        v.rolling(lw, min_periods=4).quantile(0.75) - v.rolling(lw, min_periods=4).quantile(0.25)
    ).fillna(0.0)

    feats = pd.DataFrame(
        {
            "run_id": g["series"],
            "window_start": g["timestamp"].astype(str),
            "window_end": g["timestamp"].astype(str),
            "value": v,
            "residual": v - roll_mean,
            "abs_residual": (v - roll_mean).abs(),
            "local_z": ((v - roll_mean) / roll_std.replace(0.0, np.nan)).fillna(0.0),
            "roll_std": roll_std,
            "range": roll_max - roll_min,
            "dist_from_median": (v - roll_med).abs(),
            "baseline_residual": v - long_med,
            "baseline_z": ((v - long_med) / long_iqr.replace(0.0, np.nan)).fillna(0.0),
            "value_delta": v.diff().fillna(0.0),
            "value_delta_abs": v.diff().abs().fillna(0.0),
            "is_anomaly": g["is_anomaly"],
        }
    )
    return feats.replace([np.inf, -np.inf], 0.0)


NAB_FEATURE_COLUMNS: tuple[str, ...] = (
    "value",
    "residual",
    "abs_residual",
    "local_z",
    "roll_std",
    "range",
    "dist_from_median",
    "baseline_residual",
    "baseline_z",
    "value_delta",
    "value_delta_abs",
)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "download":
        download_nab(force="--force" in sys.argv)
    else:
        print(__doc__)
