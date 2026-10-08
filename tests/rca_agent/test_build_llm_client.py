"""Provider selection in ``build_llm_client``."""

from __future__ import annotations

import pytest

from rca_agent.config import Settings
from rca_agent.llm import GroqLlmClient, MockLlmClient, build_llm_client
from rca_agent.llm.base import LlmConfigurationError


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings()


def test_mock_mode_returns_the_deterministic_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(build_llm_client(_settings(monkeypatch, RCA_MODE="mock")), MockLlmClient)


def test_live_groq_with_a_key_returns_the_groq_client(monkeypatch: pytest.MonkeyPatch) -> None:
    client_stub = object.__new__(GroqLlmClient)
    client_stub.model = "llama-3.3-70b-versatile"
    monkeypatch.setattr(
        GroqLlmClient,
        "from_settings",
        classmethod(lambda cls, settings: client_stub),
    )
    client = build_llm_client(
        _settings(
            monkeypatch,
            RCA_MODE="live",
            LLM_PROVIDER="groq",
            LLM_API_KEY="gsk-placeholder",
        )
    )
    assert client is client_stub
    assert client.provider == "groq"


def test_live_without_a_key_is_a_configuration_error_not_a_mock_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(LlmConfigurationError):
        build_llm_client(
            _settings(monkeypatch, RCA_MODE="live", LLM_PROVIDER="groq", LLM_API_KEY="")
        )


def test_live_with_an_unknown_provider_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(LlmConfigurationError) as exc:
        build_llm_client(
            _settings(monkeypatch, RCA_MODE="live", LLM_PROVIDER="openai", LLM_API_KEY="x")
        )
    assert "groq" in str(exc.value)


def test_live_mode_never_silently_downgrades_to_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    for provider in ("mistral", "local", "mock"):
        with pytest.raises(LlmConfigurationError):
            build_llm_client(
                _settings(monkeypatch, RCA_MODE="live", LLM_PROVIDER=provider, LLM_API_KEY="x")
            )


def test_configured_model_flows_through(monkeypatch: pytest.MonkeyPatch) -> None:
    client_stub = object.__new__(GroqLlmClient)

    def client_from_settings(cls: type[GroqLlmClient], settings: Settings) -> GroqLlmClient:
        client_stub.model = settings.llm.model
        return client_stub

    monkeypatch.setattr(GroqLlmClient, "from_settings", classmethod(client_from_settings))
    client = build_llm_client(
        _settings(
            monkeypatch,
            RCA_MODE="live",
            LLM_PROVIDER="groq",
            LLM_API_KEY="gsk-x",
            LLM_MODEL="llama-3.3-70b-versatile",
        )
    )
    assert isinstance(client, GroqLlmClient)
    assert client.model == "llama-3.3-70b-versatile"
