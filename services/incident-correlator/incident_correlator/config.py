"""incident-correlator configuration.

Same convention as the rest of the platform: typed ``pydantic-settings``, one
prefix per concern, ``.env`` for local dev, nothing reads ``os.environ``
directly. Correlation and severity have their own config objects (see
``correlation.py`` / ``severity.py``); this module wires the rest.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from incident_correlator import SERVICE_NAME
from incident_correlator.correlation import CorrelationConfig
from incident_correlator.severity import SeverityConfig
from incident_correlator.topology import TopologyConfig


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
    anomaly_dlq_topic: str = "anomaly.events.dlq"
    incident_topic: str = "incident.events"
    consumer_group: str = "incident-correlator"
    client_id: str = SERVICE_NAME
    auto_create_topics: bool = True

    max_retries: int = 3
    retry_backoff_seconds: float = 2.0


class DbSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DB_", env_file=".env", extra="ignore")

    # asyncpg for the app, driver stripped for Alembic. Local dev creds only.
    url: str = "postgresql+asyncpg://sentinelops:sentinelops@localhost:5432/sentinelops"
    echo: bool = False
    pool_size: int = 5
    pool_max_overflow: int = 5


class OTelSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OTEL_", env_file=".env", extra="ignore")

    service_name: str = SERVICE_NAME
    exporter_otlp_endpoint: str | None = None
    traces_console_export: bool = False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app: AppSettings = Field(default_factory=AppSettings)
    kafka: KafkaSettings = Field(default_factory=KafkaSettings)
    db: DbSettings = Field(default_factory=DbSettings)
    otel: OTelSettings = Field(default_factory=OTelSettings)
    correlation: CorrelationConfig = Field(default_factory=CorrelationConfig)
    severity: SeverityConfig = Field(default_factory=SeverityConfig)
    topology: TopologyConfig = Field(default_factory=TopologyConfig)


@lru_cache
def get_settings() -> Settings:
    return Settings()
