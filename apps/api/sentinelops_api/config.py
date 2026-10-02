"""Application configuration.

Settings are loaded from environment variables (and an optional local ``.env``
file). This module establishes the environment-variable convention that later
phases extend: every new subsystem adds its own typed settings here rather than
reading ``os.environ`` directly elsewhere in the codebase.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    """JWT settings (Phase 10.1 — ``sentinelops_api.auth``).

    The signing key is deliberately required. A deployment with no explicit
    secret must fail during startup rather than silently sharing a public demo
    key.
    """

    model_config = SettingsConfigDict(env_prefix="JWT_", env_file=".env", extra="ignore")

    secret_key: SecretStr
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30


class Settings(BaseSettings):
    """Runtime configuration for the API application."""

    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "development"
    # 0.0.0.0 is intentional: the app is meant to be reached from outside its
    # container. Restrict this via APP_HOST for host-only local runs.
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"
    auth: AuthSettings = Field(default_factory=lambda: AuthSettings())  # type: ignore[call-arg]


@lru_cache
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance."""

    return Settings()
