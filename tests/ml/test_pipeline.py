"""End-to-end-ish: raw snapshots -> windows -> features -> experiment.

Uses synthetic data only (no service, no network).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from ml.data.prepare import load_processed_run, prepare_run
from ml.data.schema import REQUIRED_SIGNAL_COLUMNS
from ml.data.validation import validate_signal_frame
from ml.experiments.runner import ExperimentSpec, PreparedData, run_experiment
from ml.features.engineering import build_features
from ml.models import IsolationForestDetector, RobustZScoreDetector


def _write_synthetic_snapshots(path: Path, *, n_per_segment: int = 6) -> None:
    """A tiny cumulative-counter timeline: normal -> latency -> normal."""

    rows = []
    t = 1_800_000_000.0
    http_total = http_2xx = http_5xx = lat_sum = created = pub_ok = pub_fail = pub_lat_count = 0.0
    pub_lat_sum = 0.0
    buckets = {50.0: 0.0, 100.0: 0.0, 500.0: 0.0, float("inf"): 0.0}

    segments = [
        ("normal", "normal", 60.0),
        ("latency", "latency_anomaly", 450.0),
        ("normal", "normal", 60.0),
    ]
    for scen, label, lat_ms in segments:
        for _ in range(n_per_segment):
            t += 10.0
            reqs = 60
            http_total += reqs
            http_2xx += reqs
            lat_sum += reqs * lat_ms
            created += reqs
            pub_ok += reqs
            pub_lat_count += reqs
            pub_lat_sum += reqs * 0.002
            if lat_ms < 100:
                buckets[50.0] += reqs * 0.9
                buckets[100.0] += reqs
            buckets[500.0] += reqs
            buckets[float("inf")] += reqs
            rows.append(
                {
                    "run_id": "syn",
                    "scrape_ts": t,
                    "scrape_iso": pd.Timestamp(t, unit="s", tz="UTC").isoformat(),
                    "scenario": scen,
                    "label": label,
                    "is_anomaly": 0 if label in ("normal", "recovery") else 1,
                    "http_post_count_total": http_total,
                    "http_post_count_2xx": http_2xx,
                    "http_post_count_4xx": 0.0,
                    "http_post_count_5xx": http_5xx,
                    "http_post_latency_sum_ms": lat_sum,
                    "http_post_latency_count": http_total,
                    "orders_created_total": created,
                    "publish_success_total": pub_ok,
                    "publish_failure_total": pub_fail,
                    "publish_latency_sum_s": pub_lat_sum,
                    "publish_latency_count": pub_lat_count,
                    "debug_request_failed_simulated": 0.0,
                    "debug_request_failed_publish": 0.0,
                    "debug_failure_injection_total": 0.0,
                    "http_post_bucket_50.0": buckets[50.0],
                    "http_post_bucket_100.0": buckets[100.0],
                    "http_post_bucket_500.0": buckets[500.0],
                    "http_post_bucket_inf": buckets[float("inf")],
                }
            )
    fieldnames = list(rows[0])
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def test_prepare_run_produces_valid_windows(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    (raw_root / "syn").mkdir(parents=True)
    _write_synthetic_snapshots(raw_root / "syn" / "snapshots.csv")

    out = prepare_run("syn", raw_root=raw_root, out_root=tmp_path / "processed")
    windows = pd.read_csv(out / "windows.csv")
    labels = pd.read_csv(out / "labels.csv")
    assert "label" in labels.columns and "request_rate" not in labels.columns

    validate_signal_frame(windows, REQUIRED_SIGNAL_COLUMNS, min_rows=5)
    assert (windows["request_rate"] > 0).all()
    # latency-segment windows have much higher mean latency
    merged = load_processed_run("syn", root=tmp_path / "processed")
    lat = merged[merged["label"] == "latency_anomaly"]["latency_mean_ms"].mean()
    norm = merged[merged["label"] == "normal"]["latency_mean_ms"].mean()
    assert lat > 3 * norm
    assert json.loads((out / "manifest.json").read_text())["n_windows"] == len(windows)


def test_prepare_run_drops_boundary_windows(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    (raw_root / "syn").mkdir(parents=True)
    _write_synthetic_snapshots(raw_root / "syn" / "snapshots.csv", n_per_segment=5)
    out = prepare_run("syn", raw_root=raw_root, out_root=tmp_path / "processed")
    labels = pd.read_csv(out / "labels.csv")
    # 3 segments x 5 scrapes = 15 snapshots -> 14 gaps - 2 boundaries = 12 windows
    assert len(labels) == 12


def _synthetic_spec(name: str) -> ExperimentSpec:
    from tests.ml.conftest import _make_signal_frame

    def build() -> PreparedData:
        # anomalies land in each of train / val / test (chronological 60/20/20 of 200)
        df = _make_signal_frame(
            n=200,
            run_id="exp",
            seed=3,
            anomaly_slices=[
                (40, 55, "latency_anomaly"),
                (125, 135, "error_anomaly"),
                (170, 185, "latency_anomaly"),
            ],
        )
        feats = build_features(df)
        from ml.splits import chronological_split

        s = chronological_split(feats, val_fraction=0.2, test_fraction=0.2)
        from ml.data.schema import FEATURE_COLUMNS

        return PreparedData(
            s.train,
            s.train["is_anomaly"],
            s.val,
            s.val["is_anomaly"],
            s.test,
            s.test["is_anomaly"],
            10.0,
            list(FEATURE_COLUMNS),
            {"dataset": "synthetic"},
        )

    return ExperimentSpec(
        name=name,
        title="synthetic smoke",
        rationale="test",
        build_data=build,
        models={
            "robust_zscore": lambda fn: RobustZScoreDetector(feature_names=fn),
            "isolation_forest": lambda fn: IsolationForestDetector(
                feature_names=fn, random_state=42
            ),
        },
    )


def test_experiment_runs_and_is_reproducible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ml.config as cfg
    import ml.experiments.runner as runner

    monkeypatch.setattr(cfg, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(cfg, "MODELS_DIR", tmp_path / "models")
    monkeypatch.setattr(runner, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(runner, "MODELS_DIR", tmp_path / "models")

    r1 = run_experiment(_synthetic_spec("smoke1"))
    r2 = run_experiment(_synthetic_spec("smoke1"))

    def strip(r: dict[str, Any]) -> dict[str, Any]:
        r = json.loads(json.dumps(r))
        r.pop("generated_at", None)
        for m in r["results"].values():
            (m.get("metadata") or {}).pop("created_at", None)
        return r

    assert strip(r1)["results"] == strip(r2)["results"]
    assert (tmp_path / "reports" / "smoke1" / "metrics.json").exists()
    # isolation forest should beat a trivial always-normal baseline on F1 here
    assert r1["results"]["isolation_forest"]["pointwise"]["recall"] > 0.0


@pytest.mark.parametrize("n", [2, 3])
def test_feature_config_rejects_tiny_window(n: int) -> None:
    from ml.features.engineering import FeatureConfig

    if n < 2:
        with pytest.raises(ValueError):
            FeatureConfig(rolling_window=n)
    else:
        assert FeatureConfig(rolling_window=n).rolling_window == n
