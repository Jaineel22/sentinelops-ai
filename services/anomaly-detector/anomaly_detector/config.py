"""anomaly-detector configuration (same conventions as the rest of the platform)."""

from __future__ import annotations

import os
from functools import lru_cache

from ml.mlops.config import MLflowSettings
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from anomaly_detector import SERVICE_NAME


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"


class KafkaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KAFKA_", env_file=".env", extra="ignore")

    bootstrap_servers: str = "localhost:29092"
    anomaly_topic: str = "anomaly.events"
    client_id: str = SERVICE_NAME
    auto_create_topics: bool = True


class DetectorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DETECTOR_", env_file=".env", extra="ignore")

    # Where the telemetry to score comes from, and what it describes.
    target_metrics_url: str = "http://localhost:8000/metrics"
    target_service: str = "orders-service"
    environment: str = "development"

    # Poll cadence. 10s matches the Phase 2 collector's scrape step, so live
    # windows have the same width the model was trained on.
    poll_interval_seconds: float = 10.0

    # Trained model cache. Missing -> trained once at startup from the committed
    # run_a dataset with a fixed seed, then written here.
    model_path: str = "artifacts/models/detector_stream.joblib"
    seed: int = 42

    # Only publish windows the model flags. Set false to emit every scored window
    # (useful for debugging the pipeline).
    publish_only_anomalies: bool = True

    # Phase 6C: when `MLFLOW_TRACKING_URI` is set in the environment, the model is
    # resolved from the MLflow registry by alias (default `champion`) instead of
    # `model_path`. Populated by `from_env` — never auto-read (distinct prefix).
    mlflow: MLflowSettings | None = None

    @classmethod
    def from_env(cls) -> DetectorSettings:
        """Build from the environment, attaching `MLflowSettings` only when the
        registry is opted into via `MLFLOW_TRACKING_URI`."""

        base = cls()
        if os.environ.get("MLFLOW_TRACKING_URI"):
            return base.model_copy(update={"mlflow": MLflowSettings()})
        return base


class OTelSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OTEL_", env_file=".env", extra="ignore")

    service_name: str = SERVICE_NAME
    exporter_otlp_endpoint: str | None = None
    traces_console_export: bool = False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app: AppSettings = Field(default_factory=AppSettings)
    kafka: KafkaSettings = Field(default_factory=KafkaSettings)
    detector: DetectorSettings = Field(default_factory=DetectorSettings.from_env)
    otel: OTelSettings = Field(default_factory=OTelSettings)


@lru_cache
def get_settings() -> Settings:
    return Settings()
