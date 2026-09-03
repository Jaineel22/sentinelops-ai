"""remediation-controller configuration (Phase 5C).

Same convention as ``incident_correlator.config`` / ``rca_agent.config``: typed
``pydantic-settings``, one env prefix per concern, ``.env`` for local dev, nothing
reads ``os.environ`` directly.

The **policy** configuration is deliberately NOT here — it is code-defined and
immutable (ADR-025). Only process / database / observability settings live in
this module.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from remediation_controller import SERVICE_NAME


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    # Phase 5F: operational timing for recovery verification. These change *how
    # long / how often* the verifier looks — they cannot make a degraded service
    # pass (the safety thresholds are code-defined in
    # ``remediation_controller.recovery.config``, ADR-025 discipline).
    verification_timeout_seconds: int = 30
    verification_poll_interval_seconds: float = 3.0


class KafkaSettings(BaseSettings):
    """Phase 5G: outbound remediation lifecycle events only.

    The remediation-controller **publishes** ``remediation.events`` and consumes
    no topic (ADR-030). ``enabled=false`` (or an unreachable broker) degrades to
    audit-trail-only — the approval workflow does not depend on Kafka.
    """

    model_config = SettingsConfigDict(env_prefix="KAFKA_", env_file=".env", extra="ignore")

    enabled: bool = True
    bootstrap_servers: str = "localhost:29092"
    remediation_topic: str = "remediation.events"
    client_id: str = SERVICE_NAME
    auto_create_topics: bool = True


class DbSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DB_", env_file=".env", extra="ignore")

    # Shared PostgreSQL instance + database with the incident-correlator and
    # rca-agent; the remediation-controller owns its own tables and its own
    # Alembic lineage (``alembic_version_remediation``) — same pattern as ADR-019.
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
    db: DbSettings = Field(default_factory=DbSettings)
    kafka: KafkaSettings = Field(default_factory=KafkaSettings)
    otel: OTelSettings = Field(default_factory=OTelSettings)


@lru_cache
def get_settings() -> Settings:
    return Settings()
