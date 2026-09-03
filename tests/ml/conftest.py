"""Deterministic synthetic fixtures for ML tests.

No test needs a running service, Kafka, or the network. Synthetic signal frames
have a clear normal regime and injected anomalous regions so detector behaviour
is checkable.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from ml.data.schema import SIGNAL_COLUMNS
from ml.mlops.config import MLflowSettings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def mlflow_sqlite(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    """A local sqlite-backed MLflow tracking + registry store shared across the
    Phase 6 MLOps tests (the first use pays the one-time schema migration)."""

    directory = tmp_path_factory.mktemp("mlflow_store")
    return directory, f"sqlite:///{(directory / 'mlflow.db').as_posix()}"


def make_model_version(
    settings: MLflowSettings,
    *,
    f1: float = 0.82,
    recall: float = 1.0,
    pr_auc: float = 0.70,
    precision: float = 0.70,
) -> tuple[str, str]:
    """Create an MLflow run (metrics + a stub model bundle artifact) and register
    it as a new version. Returns ``(version, run_id)``. Requires ``mlflow`` and a
    working-directory the caller has pointed at a temp store."""

    import mlflow
    from ml.mlops.registry import register_model

    mlflow.set_tracking_uri(settings.tracking_uri)
    mlflow.set_experiment(settings.experiment_name)
    with mlflow.start_run() as run:
        mlflow.log_metrics(
            {
                "pointwise.f1": f1,
                "pointwise.recall": recall,
                "pointwise.pr_auc": pr_auc,
                "pointwise.precision": precision,
            }
        )
        bundle = Path("bundle.joblib")
        bundle.write_bytes(b"stub-bundle")
        mlflow.log_artifact(str(bundle), artifact_path="model")
        run_id = run.info.run_id
    version = register_model(str(bundle), run_id, settings)
    return version, run_id


def _make_signal_frame(
    *, n: int, run_id: str, seed: int, anomaly_slices: list[tuple[int, int, str]]
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2026-02-01T00:00:00Z")
    step = pd.Timedelta(seconds=10)

    base = {
        "request_rate": rng.normal(6.0, 0.3, n).clip(0.1),
        "error_rate": rng.normal(0.01, 0.005, n).clip(0, 1),
        "success_rate": np.zeros(n),
        "latency_mean_ms": rng.normal(55.0, 4.0, n).clip(1),
        "latency_p50_ms": rng.normal(50.0, 4.0, n).clip(1),
        "latency_p90_ms": rng.normal(80.0, 6.0, n).clip(1),
        "latency_p95_ms": rng.normal(95.0, 7.0, n).clip(1),
        "publish_rate": rng.normal(6.0, 0.3, n).clip(0.1),
        "publish_error_rate": rng.normal(0.0, 0.002, n).clip(0, 1),
        "publish_latency_mean_ms": rng.normal(2.0, 0.3, n).clip(0.1),
        "orders_created_rate": rng.normal(6.0, 0.3, n).clip(0.1),
    }
    df = pd.DataFrame(base)
    labels = np.array(["normal"] * n, dtype=object)

    lat_cols = ["latency_mean_ms", "latency_p50_ms", "latency_p90_ms", "latency_p95_ms"]
    for lo, hi, kind in anomaly_slices:
        labels[lo:hi] = kind
        if kind == "latency_anomaly":
            df.loc[lo : hi - 1, lat_cols] *= 8.0
        elif kind == "error_anomaly":
            df.loc[lo : hi - 1, "error_rate"] = rng.uniform(0.2, 0.35, hi - lo)
        elif kind == "publish_failure":
            df.loc[lo : hi - 1, "publish_error_rate"] = rng.uniform(0.2, 0.35, hi - lo)
        elif kind == "traffic_surge":
            df.loc[lo : hi - 1, ["request_rate", "publish_rate", "orders_created_rate"]] *= 4.0

    df["success_rate"] = (1.0 - df["error_rate"]).clip(0, 1)
    df.insert(0, "run_id", run_id)
    df.insert(1, "window_start", [(start + i * step).isoformat() for i in range(n)])
    df.insert(2, "window_end", [(start + (i + 1) * step).isoformat() for i in range(n)])
    df.insert(3, "window_seconds", 10.0)
    df["label"] = labels
    df["is_anomaly"] = (labels != "normal").astype(int)
    cols = ["run_id", "window_start", "window_end", "window_seconds", *SIGNAL_COLUMNS]
    return df[[*cols, "label", "is_anomaly"]]


@pytest.fixture
def signal_frame() -> pd.DataFrame:
    """120 windows: normal with a latency region and an error region."""

    return _make_signal_frame(
        n=120,
        run_id="synthetic_a",
        seed=7,
        anomaly_slices=[(30, 45, "latency_anomaly"), (80, 95, "error_anomaly")],
    )


@pytest.fixture
def holdout_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    train = _make_signal_frame(
        n=120,
        run_id="train_run",
        seed=1,
        anomaly_slices=[(30, 45, "latency_anomaly"), (80, 95, "error_anomaly")],
    )
    test = _make_signal_frame(
        n=90,
        run_id="holdout_run",
        seed=2,
        anomaly_slices=[(25, 40, "publish_failure"), (60, 75, "traffic_surge")],
    )
    return train, test


@pytest.fixture
def metrics_text() -> str:
    return (FIXTURES / "metrics_sample.txt").read_text(encoding="utf-8")
