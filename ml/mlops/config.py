"""Typed configuration for the MLOps subsystem (Phase 6).

Same conventions as the rest of the platform: a ``pydantic-settings`` object read
from the environment (``MLFLOW_`` prefix) / ``.env``, never ``os.environ``
directly. Nothing here is a secret — the MLflow deployment in this project is
local (Docker Compose) and uses the throwaway dev PostgreSQL credentials.
"""

from __future__ import annotations

import contextlib
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def make_console_emoji_safe() -> None:
    """MLflow writes emoji to stdout on run end / model registration; a Windows
    ``cp1252`` console then raises ``UnicodeEncodeError`` and aborts the call.
    Switch stdout/stderr to a non-fatal error handler so the (cosmetic) MLflow
    lines degrade instead of killing tracking. Idempotent, best-effort, and only
    ever makes the streams *more* tolerant."""

    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if encoding in ("utf8", ""):
            continue
        with contextlib.suppress(Exception):
            stream.reconfigure(errors="backslashreplace")  # type: ignore[union-attr]


def ensure_local_tracking_store(tracking_uri: str) -> None:
    """A ``sqlite:///<path>`` tracking store needs its parent directory to exist
    before MLflow opens it (mlflow does not create it). No-op for HTTP / other
    URIs. Best-effort."""

    prefix = "sqlite:///"
    if not tracking_uri.startswith(prefix):
        return
    with contextlib.suppress(OSError):
        Path(tracking_uri[len(prefix) :]).resolve().parent.mkdir(parents=True, exist_ok=True)


class MLflowSettings(BaseSettings):
    """Where training runs are tracked and how the registered model is named.

    * ``tracking_uri`` — an MLflow tracking URI. ``sqlite:///...`` or a
      ``file:...`` store for local/offline use and tests; ``http://mlflow:5000``
      (in Compose) / ``http://localhost:5000`` (from the host) for the shared
      local server. The model registry needs a database-backed URI
      (``sqlite`` / ``postgresql`` / an HTTP server) — a bare ``file:`` store
      cannot hold registered models or aliases.
    * ``required`` — when ``True`` a component that depends on MLflow must fail
      loudly if it is unreachable; when ``False`` it may fall back explicitly
      (never silently). Consumed by the inference integration in Sub-phase 6C.
    """

    model_config = SettingsConfigDict(env_prefix="MLFLOW_", env_file=".env", extra="ignore")

    tracking_uri: str = "sqlite:///mlruns/mlflow.db"
    registered_model_name: str = "sentinelops-anomaly-detector"
    model_alias: str = "champion"
    required: bool = True
    experiment_name: str = "sentinelops-anomaly-detection"

    @field_validator("tracking_uri", "registered_model_name", "model_alias", "experiment_name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be empty")
        return value


@lru_cache
def get_mlflow_settings() -> MLflowSettings:
    return MLflowSettings()
