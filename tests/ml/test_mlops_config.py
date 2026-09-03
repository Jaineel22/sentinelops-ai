"""MLflow configuration (Phase 6A) — defaults, env/.env overrides, validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from ml.mlops.config import MLflowSettings

_ENV_VARS = (
    "MLFLOW_TRACKING_URI",
    "MLFLOW_REGISTERED_MODEL_NAME",
    "MLFLOW_MODEL_ALIAS",
    "MLFLOW_REQUIRED",
    "MLFLOW_EXPERIMENT_NAME",
)


@pytest.fixture
def clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No MLFLOW_* env vars and a working directory with no .env file."""

    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_defaults(clean_env: Path) -> None:
    settings = MLflowSettings()
    assert settings.tracking_uri == "sqlite:///mlruns/mlflow.db"
    assert settings.registered_model_name == "sentinelops-anomaly-detector"
    assert settings.model_alias == "champion"
    assert settings.required is True
    assert settings.experiment_name == "sentinelops-anomaly-detection"


def test_env_file_override(clean_env: Path) -> None:
    (clean_env / ".env").write_text(
        "MLFLOW_TRACKING_URI=http://mlflow:5000\n"
        "MLFLOW_REGISTERED_MODEL_NAME=custom-model\n"
        "MLFLOW_MODEL_ALIAS=candidate\n"
        "MLFLOW_REQUIRED=false\n"
        "MLFLOW_EXPERIMENT_NAME=custom-exp\n",
        encoding="utf-8",
    )
    settings = MLflowSettings()
    assert settings.tracking_uri == "http://mlflow:5000"
    assert settings.registered_model_name == "custom-model"
    assert settings.model_alias == "candidate"
    assert settings.required is False
    assert settings.experiment_name == "custom-exp"


def test_env_var_override(clean_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "sqlite:///tmp/other.db")
    monkeypatch.setenv("MLFLOW_REQUIRED", "false")
    settings = MLflowSettings()
    assert settings.tracking_uri == "sqlite:///tmp/other.db"
    assert settings.required is False


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_tracking_uri_rejected(clean_env: Path, blank: str) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        MLflowSettings(tracking_uri=blank)


def test_blank_experiment_name_rejected(clean_env: Path) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        MLflowSettings(experiment_name="")
