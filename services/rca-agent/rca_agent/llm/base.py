"""The LLM boundary for the investigation graph (ADR-021).

The graph never talks to a model API directly. It calls one of four typed
methods on an :class:`LlmClient`; each takes a deterministically-built request
(data, never instructions) and returns a Pydantic result that deterministic code
then validates, id-stamps, and bounds. The LLM proposes; it never decides.

Sub-phase 4C ships :class:`~rca_agent.llm.mock.MockLlmClient` (deterministic, no
network, CI-safe). The live provider client is added in Sub-phase 4D behind this
same protocol — the graph does not change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from rca_agent.domain import Confidence, FindingType, HypothesisVerdict, RecommendedActionType
from rca_agent.schemas import Evidence, Hypothesis


# --- errors --------------------------------------------------------
class LlmError(RuntimeError):
    """Base for anything that goes wrong at the model boundary."""


class LlmTimeout(LlmError):
    pass


class LlmProviderError(LlmError):
    pass


class LlmMalformedOutput(LlmError):
    pass


class LiveLlmNotAvailable(LlmError):
    """RCA_MODE=live requested, but the live provider is not wired (Sub-phase 4D)."""


class LlmConfigurationError(LlmError):
    """RCA_MODE=live requested with an unusable LLM configuration — an unknown
    ``LLM_PROVIDER`` or a missing API key. Never falls back to mock mode: a
    misconfigured live deployment must fail loudly, not run deterministically."""


# --- what the model proposes (no ids; deterministic code assigns them) ---
class PlannedCall(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool: str = Field(max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=500)


class ProposedFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    statement: str = Field(max_length=2000)
    type: FindingType
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: Confidence = Confidence.UNKNOWN


class ProposedHypothesis(BaseModel):
    model_config = ConfigDict(frozen=True)

    statement: str = Field(max_length=2000)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    assessment: str = Field(default="", max_length=2000)


class ProposedRootCause(BaseModel):
    model_config = ConfigDict(frozen=True)

    statement: str = Field(max_length=2000)
    confidence: Confidence
    evidence_ids: list[str] = Field(default_factory=list)
    reasoning_summary: str = Field(default="", max_length=4000)


class ProposedAction(BaseModel):
    model_config = ConfigDict(frozen=True)

    action_type: RecommendedActionType
    target_service: str | None = Field(default=None, max_length=128)
    description: str = Field(max_length=2000)
    rationale: str = Field(default="", max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list)


class ProposedTimelineEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    at: datetime
    description: str = Field(max_length=1000)
    evidence_ids: list[str] = Field(default_factory=list)


# --- results ------------------------------------------------------
class PlanResult(BaseModel):
    calls: list[PlannedCall] = Field(default_factory=list)
    rationale: str = Field(default="", max_length=2000)


class AnalysisResult(BaseModel):
    findings: list[ProposedFinding] = Field(default_factory=list)
    hypotheses: list[ProposedHypothesis] = Field(default_factory=list)
    notes: str = Field(default="", max_length=2000)


class HypothesisVerdictItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int = Field(ge=0)
    verdict: HypothesisVerdict
    assessment: str = Field(default="", max_length=2000)


class VerificationResult(BaseModel):
    verdicts: list[HypothesisVerdictItem] = Field(default_factory=list)
    needs_more_evidence: bool = False
    additional_calls: list[PlannedCall] = Field(default_factory=list)
    ready_to_conclude: bool = True
    notes: str = Field(default="", max_length=2000)


class SynthesisResult(BaseModel):
    conclusion: Literal["completed", "insufficient_evidence"]
    summary: str = Field(max_length=4000)
    root_cause: ProposedRootCause | None = None
    contributing_factors: list[ProposedFinding] = Field(default_factory=list)
    recommended_action: ProposedAction
    overall_confidence: Confidence = Confidence.UNKNOWN
    uncertainty: str = Field(max_length=4000)
    timeline: list[ProposedTimelineEntry] = Field(default_factory=list)


# --- requests (deterministically built; carry data, not instructions) ---
class _Request(BaseModel):
    model_config = ConfigDict(frozen=True)

    incident: dict[str, Any]
    evidence: list[Evidence] = Field(default_factory=list)
    repair_errors: list[str] = Field(default_factory=list)


class PlanRequest(_Request):
    tool_specs: list[dict[str, Any]] = Field(default_factory=list)


class AnalyzeRequest(_Request):
    pass


class VerifyRequest(_Request):
    findings: list[ProposedFinding] = Field(default_factory=list)
    hypotheses: list[ProposedHypothesis] = Field(default_factory=list)
    reanalysis_allowed: bool = False


class SynthesizeRequest(_Request):
    investigation_id: str
    findings: list[ProposedFinding] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)


@runtime_checkable
class LlmClient(Protocol):
    provider: str
    model: str | None

    async def plan(self, request: PlanRequest) -> PlanResult: ...

    async def analyze(self, request: AnalyzeRequest) -> AnalysisResult: ...

    async def verify(self, request: VerifyRequest) -> VerificationResult: ...

    async def synthesize(self, request: SynthesizeRequest) -> SynthesisResult: ...
