"""MLflow tracking helpers (Phase 6A).

The lineage helpers are pure and always tested. The functions that touch MLflow
run against a temporary local ``sqlite://`` store (no server) and are skipped if
``mlflow`` is not installed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from ml.mlops.config import MLflowSettings
from ml.mlops.tracking import get_git_sha, get_package_versions, log_run, setup_experiment

_HAS_MLFLOW = importlib.util.find_spec("mlflow") is not None
_needs_mlflow = pytest.mark.skipif(not _HAS_MLFLOW, reason="mlflow not installed")


def test_get_git_sha_returns_string() -> None:
    sha = get_git_sha()
    assert isinstance(sha, str)
    assert sha  # non-empty: a real SHA in the repo, or the literal "unknown"


def test_get_package_versions() -> None:
    versions = get_package_versions()
    assert isinstance(versions, dict)
    assert "numpy" in versions and "pandas" in versions
    assert "mlflow" in versions or "mlflow-skinny" in versions


def _local_settings(tmp_path: Path) -> MLflowSettings:
    return MLflowSettings(
        tracking_uri=f"sqlite:///{tmp_path.as_posix()}/mlflow.db",
        experiment_name="phase6a-test",
    )


@_needs_mlflow
def test_setup_experiment_creates_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    settings = _local_settings(tmp_path)

    first = setup_experiment(settings)
    second = setup_experiment(settings)

    assert first and first == second


@_needs_mlflow
def test_log_run_records_params_metrics_and_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mlflow
    from mlflow.tracking import MlflowClient

    monkeypatch.chdir(tmp_path)
    settings = _local_settings(tmp_path)

    report_dir = tmp_path / "report"
    report_dir.mkdir()
    (report_dir / "metrics.json").write_text('{"ok": true}', encoding="utf-8")
    model_path = tmp_path / "model.joblib"
    model_path.write_bytes(b"not-a-real-model")

    experiment_spec = {
        "experiment": "expX",
        "run_name": "expX__isolation_forest",
        "model_type": "isolation_forest",
        "random_seed": 42,
        "hyperparameters": {"n_estimators": 200},
    }
    metrics = {
        "threshold": 0.1,
        "pointwise": {"precision": 0.7, "recall": 1.0, "f1": 0.82, "flagged": True},
        "eventwise": {"event_recall": 1.0, "mean_detection_delay_seconds": None},
    }

    run_id = log_run(
        experiment_spec, metrics, report_dir, model_path, settings, extra_params={"n_train": 72}
    )
    assert run_id

    mlflow.set_tracking_uri(settings.tracking_uri)
    client = MlflowClient()
    run = client.get_run(run_id)

    assert run.data.params["model_type"] == "isolation_forest"
    assert run.data.params["hyperparameters.n_estimators"] == "200"
    assert run.data.params["n_train"] == "72"
    assert "lineage.git_sha" in run.data.params
    assert run.data.metrics["pointwise.precision"] == pytest.approx(0.7)
    assert run.data.metrics["pointwise.f1"] == pytest.approx(0.82)
    assert "pointwise.flagged" not in run.data.metrics  # bool is not a metric

    artifacts = {a.path for a in client.list_artifacts(run_id)}
    assert {"report", "model", "experiment_spec.json"} <= artifacts


def test_log_run_is_fail_safe_on_bad_uri(tmp_path: Path) -> None:
    settings = MLflowSettings(tracking_uri="http://127.0.0.1:1/unreachable", experiment_name="x")
    result = log_run({"experiment": "x"}, {"pointwise": {"f1": 0.5}}, None, None, settings)
    assert result is None
