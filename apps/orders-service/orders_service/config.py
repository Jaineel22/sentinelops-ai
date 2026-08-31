"""orders-service configuration.

Follows the Phase 0 convention: all runtime configuration comes from environment
variables through a typed ``pydantic-settings`` object. Nothing in the codebase
reads ``os.environ`` directly.

Three concerns, three env prefixes:

* ``APP_``    — process/runtime basics (shared convention with the platform API)
* ``KAFKA_``  — the event backbone
* ``OTEL_``   — the observability pipeline (OpenTelemetry)
* ``ORDERS_`` — service-specific knobs, including development-only failure
  injection

The Kafka/observability split is deliberate — see
docs/decisions/adr-008-events-vs-telemetry.md.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from orders_service import SERVICE_NAME


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", extra="ignore")

    env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"


class KafkaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KAFKA_", env_file=".env", extra="ignore")

    bootstrap_servers: str = "localhost:29092"
    orders_topic: str = "orders.events"
    client_id: str = SERVICE_NAME

    # Topic provisioning for local/dev. A real deployment manages topics out of
    # band (Terraform / an ops runbook); Phase 1 keeps it self-contained.
    topic_partitions: int = 1
    topic_replication_factor: int = 1
    auto_create_topic: bool = True

    # Producer reliability. `acks=all` + bounded retries gives at-least-once
    # delivery at the API boundary without a full outbox (see ADR-010).
    acks: str = "all"
    enable_idempotence: bool = True
    request_timeout_ms: int = 5000
    # Hard ceiling on how long a publish may block an HTTP request.
    publish_timeout_seconds: float = 6.0


class OTelSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OTEL_", env_file=".env", extra="ignore")

    service_name: str = SERVICE_NAME
    # If set (e.g. http://localhost:4318), traces are exported via OTLP/HTTP.
    # Left unset in Phase 1: no collector is deployed yet (that is Phase 7).
    exporter_otlp_endpoint: str | None = None
    # Opt-in console span export for local inspection without a collector.
    traces_console_export: bool = False
    # Prometheus scrape endpoint exposed by the app.
    metrics_path: str = "/metrics"


class SimulationSettings(BaseSettings):
    """Development-only failure injection. Disabled by default.

    Guarded at startup: if ``APP_ENV=production`` and any knob is non-default,
    the service refuses to start (see ``validate_for_env``).
    """

    model_config = SettingsConfigDict(env_prefix="ORDERS_", env_file=".env", extra="ignore")

    simulate_latency_ms: int = Field(default=0, ge=0, le=60_000)
    simulate_error_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    simulate_publish_error_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    # Whether the dev-only /admin/simulation endpoint is mounted.
    sim_admin_enabled: bool = True

    @property
    def any_injection_enabled(self) -> bool:
        return (
            self.simulate_latency_ms > 0
            or self.simulate_error_rate > 0.0
            or self.simulate_publish_error_rate > 0.0
        )


class Settings(BaseSettings):
    """Aggregate settings object handed to the application."""

    model_config = SettingsConfigDict(extra="ignore")

    app: AppSettings = Field(default_factory=AppSettings)
    kafka: KafkaSettings = Field(default_factory=KafkaSettings)
    otel: OTelSettings = Field(default_factory=OTelSettings)
    simulation: SimulationSettings = Field(default_factory=SimulationSettings)

    def validate_for_env(self) -> None:
        if self.app.env == "production":
            if self.simulation.any_injection_enabled:
                raise RuntimeError(
                    "Failure injection (ORDERS_SIMULATE_*) must not be enabled when "
                    "APP_ENV=production. Refusing to start."
                )
            if self.simulation.sim_admin_enabled:
                raise RuntimeError(
                    "The /admin/simulation endpoint must be disabled when "
                    "APP_ENV=production. Set ORDERS_SIM_ADMIN_ENABLED=false."
                )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_for_env()
    return settings
