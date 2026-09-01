"""rca-agent configuration.

Same conventions as ``incident_correlator.config``: typed ``pydantic-settings``,
one env prefix per concern, ``.env`` for local dev, nothing reads ``os.environ``
directly. Secrets are ``SecretStr`` and never logged.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from rca_agent import SERVICE_NAME
from rca_agent.limits import ResourceLimits


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"


class KafkaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KAFKA_", env_file=".env", extra="ignore")

    bootstrap_servers: str = "localhost:29092"
    incident_topic: str = "incident.events"
    incident_dlq_topic: str = "incident.events.dlq"
    consumer_group: str = "rca-agent"
    client_id: str = SERVICE_NAME
    auto_create_topics: bool = True
    max_retries: int = 3
    retry_backoff_seconds: float = 2.0


class DbSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DB_", env_file=".env", extra="ignore")

    # Shared PostgreSQL instance + database with the incident-correlator; the
    # rca-agent owns its own tables and its own Alembic lineage (ADR-019).
    url: str = "postgresql+asyncpg://sentinelops:sentinelops@localhost:5432/sentinelops"
    echo: bool = False
    pool_size: int = 5
    pool_max_overflow: int = 5


class LlmSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_", env_file=".env", extra="ignore")

    provider: str = "mock"  # "mock" | "anthropic" (used only when RCA_MODE=live)
    model: str | None = None  # None -> the provider client's default
    api_key: SecretStr | None = None
    base_url: str | None = None
    max_output_tokens: int = 4096
    request_timeout_seconds: float = 60.0
    # Bounded transient-failure retries inside the provider SDK (connection
    # errors / 429 / 5xx). Small and explicit — malformed model output is never
    # retried here (the engine owns the one bounded repair pass).
    max_retries: int = 2
    # Hard ceiling on the assembled prompt handed to the provider. Evidence is
    # already bounded by ResourceLimits; this is the second, explicit guard so a
    # live request can never send an unbounded prompt.
    max_prompt_chars: int = 200_000


class RcaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RCA_", env_file=".env", extra="ignore")

    # "mock" -> deterministic, no network, CI-safe. "live" -> real LLM provider.
    mode: Literal["mock", "live"] = "mock"

    incident_api_base_url: str = "http://localhost:8002"
    http_timeout_seconds: float = 10.0

    # Investigation bounds (see rca_agent.limits.ResourceLimits).
    max_tool_calls: int = 12
    max_investigation_steps: int = 25
    max_evidence_items: int = 40
    investigation_timeout_seconds: float = 120.0
    max_hypotheses: int = 5

    # Instrumented services the read-only metric/health tools may query. Only
    # services that actually exist belong here — the agent cannot reach anything
    # not listed. JSON in the env var.
    service_metrics_urls: dict[str, str] = Field(
        default_factory=lambda: {"orders-service": "http://orders-service:8000/metrics"}
    )
    service_health_urls: dict[str, str] = Field(
        default_factory=lambda: {"orders-service": "http://orders-service:8000"}
    )

    def resource_limits(self) -> ResourceLimits:
        return ResourceLimits(
            max_tool_calls=self.max_tool_calls,
            max_steps=self.max_investigation_steps,
            max_evidence_items=self.max_evidence_items,
            timeout_seconds=self.investigation_timeout_seconds,
            max_hypotheses=self.max_hypotheses,
        )


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
    llm: LlmSettings = Field(default_factory=LlmSettings)
    rca: RcaSettings = Field(default_factory=RcaSettings)
    otel: OTelSettings = Field(default_factory=OTelSettings)


@lru_cache
def get_settings() -> Settings:
    return Settings()
