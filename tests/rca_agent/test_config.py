"""rca-agent configuration foundation."""

from __future__ import annotations

import pytest

from rca_agent.config import RcaSettings, Settings, get_settings
from rca_agent.limits import ResourceLimits


def test_defaults() -> None:
    settings = Settings()
    assert settings.rca.mode == "mock"  # CI-safe default
    assert settings.llm.provider == "mock"
    assert settings.kafka.consumer_group == "rca-agent"
    assert settings.kafka.incident_topic == "incident.events"
    assert "orders-service" in settings.rca.service_metrics_urls


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_rca_settings_build_resource_limits() -> None:
    rca = RcaSettings(max_tool_calls=5, investigation_timeout_seconds=30.0)
    limits = rca.resource_limits()
    assert isinstance(limits, ResourceLimits)
    assert limits.max_tool_calls == 5
    assert limits.timeout_seconds == 30.0


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RCA_MODE", "live")
    monkeypatch.setenv("RCA_MAX_TOOL_CALLS", "3")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    settings = Settings()
    assert settings.rca.mode == "live"
    assert settings.rca.max_tool_calls == 3
    assert settings.llm.provider == "anthropic"


def test_api_key_is_a_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "super-secret-value")
    settings = Settings()
    assert settings.llm.api_key is not None
    assert "super-secret-value" not in repr(settings.llm)
    assert "super-secret-value" not in str(settings.llm.api_key)
    assert settings.llm.api_key.get_secret_value() == "super-secret-value"


def test_invalid_mode_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RCA_MODE", "turbo")
    with pytest.raises(ValueError):
        Settings()
