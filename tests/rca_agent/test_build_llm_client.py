"""Provider selection in ``build_llm_client`` (Sub-phase 4D)."""

from __future__ import annotations

import pytest

from rca_agent.config import Settings
from rca_agent.llm import AnthropicLlmClient, MockLlmClient, build_llm_client
from rca_agent.llm.base import LlmConfigurationError


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings()


def test_mock_mode_returns_the_deterministic_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(build_llm_client(_settings(monkeypatch, RCA_MODE="mock")), MockLlmClient)


def test_live_anthropic_with_a_key_returns_the_anthropic_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = build_llm_client(
        _settings(
            monkeypatch,
            RCA_MODE="live",
            LLM_PROVIDER="anthropic",
            LLM_API_KEY="sk-ant-placeholder",
        )
    )
    assert isinstance(client, AnthropicLlmClient)
    assert client.provider == "anthropic"


def test_live_without_a_key_is_a_configuration_error_not_a_mock_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(LlmConfigurationError):
        build_llm_client(
            _settings(monkeypatch, RCA_MODE="live", LLM_PROVIDER="anthropic", LLM_API_KEY="")
        )


def test_live_with_an_unknown_provider_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(LlmConfigurationError) as exc:
        build_llm_client(
            _settings(monkeypatch, RCA_MODE="live", LLM_PROVIDER="openai", LLM_API_KEY="x")
        )
    assert "anthropic" in str(exc.value)


def test_live_mode_never_silently_downgrades_to_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    for provider in ("mistral", "local", "mock"):
        with pytest.raises(LlmConfigurationError):
            build_llm_client(
                _settings(monkeypatch, RCA_MODE="live", LLM_PROVIDER=provider, LLM_API_KEY="x")
            )


def test_configured_model_flows_through(monkeypatch: pytest.MonkeyPatch) -> None:
    client = build_llm_client(
        _settings(
            monkeypatch,
            RCA_MODE="live",
            LLM_PROVIDER="anthropic",
            LLM_API_KEY="sk-ant-x",
            LLM_MODEL="claude-sonnet-5",
        )
    )
    assert isinstance(client, AnthropicLlmClient)
    assert client.model == "claude-sonnet-5"
