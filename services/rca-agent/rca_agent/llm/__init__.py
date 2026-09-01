"""LLM boundary for the investigation graph (ADR-021)."""

from __future__ import annotations

from rca_agent.config import Settings
from rca_agent.llm.anthropic_client import AnthropicLlmClient
from rca_agent.llm.base import (
    AnalysisResult,
    AnalyzeRequest,
    LiveLlmNotAvailable,
    LlmClient,
    LlmConfigurationError,
    LlmError,
    LlmMalformedOutput,
    LlmProviderError,
    LlmTimeout,
    PlannedCall,
    PlanRequest,
    PlanResult,
    SynthesisResult,
    SynthesizeRequest,
    VerificationResult,
    VerifyRequest,
)
from rca_agent.llm.mock import MockLlmClient

__all__ = [
    "AnalysisResult",
    "AnalyzeRequest",
    "AnthropicLlmClient",
    "LiveLlmNotAvailable",
    "LlmClient",
    "LlmConfigurationError",
    "LlmError",
    "LlmMalformedOutput",
    "LlmProviderError",
    "LlmTimeout",
    "MockLlmClient",
    "PlanRequest",
    "PlanResult",
    "PlannedCall",
    "SynthesisResult",
    "SynthesizeRequest",
    "VerificationResult",
    "VerifyRequest",
    "build_llm_client",
]

# LLM_PROVIDER values this build can construct a live client for.
_SUPPORTED_LIVE_PROVIDERS = frozenset({"anthropic"})


def build_llm_client(settings: Settings) -> LlmClient:
    """Select the reasoner behind the one ``LlmClient`` protocol.

    ``RCA_MODE=mock`` -> the deterministic, network-free :class:`MockLlmClient`
    (the CI default). ``RCA_MODE=live`` -> the configured live provider. A live
    request with an unknown ``LLM_PROVIDER`` or a missing key raises
    :class:`LlmConfigurationError` — it never silently falls back to mock mode,
    which would make a misconfigured production deployment look healthy.
    """

    if settings.rca.mode == "mock":
        return MockLlmClient()

    provider = settings.llm.provider.strip().lower()
    if provider not in _SUPPORTED_LIVE_PROVIDERS:
        raise LlmConfigurationError(
            f"RCA_MODE=live needs a supported LLM_PROVIDER "
            f"(one of: {', '.join(sorted(_SUPPORTED_LIVE_PROVIDERS))}); got {provider!r}"
        )
    return AnthropicLlmClient.from_settings(settings)
