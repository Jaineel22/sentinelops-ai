"""LLM test doubles for failure-injection (not collected)."""

from __future__ import annotations

from rca_agent.llm.base import (
    AnalysisResult,
    AnalyzeRequest,
    LlmError,
    PlanRequest,
    PlanResult,
    ProposedAction,
    SynthesisResult,
    SynthesizeRequest,
    VerificationResult,
    VerifyRequest,
)
from rca_agent.llm.mock import MockLlmClient


class FaultInjectingLlmClient:
    """Wraps the deterministic mock; raises ``fail_with`` the first time
    ``fail_on`` method is called, then delegates."""

    provider = "mock"
    model: str | None = None

    def __init__(self, *, fail_on: str, fail_with: LlmError) -> None:
        self._inner = MockLlmClient()
        self._fail_on = fail_on
        self._fail_with = fail_with
        self._fired = False

    def _maybe_fail(self, method: str) -> None:
        if method == self._fail_on and not self._fired:
            self._fired = True
            raise self._fail_with

    async def plan(self, request: PlanRequest) -> PlanResult:
        self._maybe_fail("plan")
        return await self._inner.plan(request)

    async def analyze(self, request: AnalyzeRequest) -> AnalysisResult:
        self._maybe_fail("analyze")
        return await self._inner.analyze(request)

    async def verify(self, request: VerifyRequest) -> VerificationResult:
        self._maybe_fail("verify")
        return await self._inner.verify(request)

    async def synthesize(self, request: SynthesizeRequest) -> SynthesisResult:
        self._maybe_fail("synthesize")
        return await self._inner.synthesize(request)


class AlwaysFailsLlmClient(FaultInjectingLlmClient):
    """Raises on every call to the target method (no ``_fired`` reset)."""

    def _maybe_fail(self, method: str) -> None:
        if method == self._fail_on:
            raise self._fail_with


class OverclaimingLlmClient:
    """Delegates planning/analysis/verification to the mock, but synthesizes an
    RCA that fails deterministic validation (root cause citing a fabricated
    evidence id and HIGH confidence with none)."""

    provider = "mock"
    model: str | None = None

    def __init__(self) -> None:
        self._inner = MockLlmClient()

    async def plan(self, request: PlanRequest) -> PlanResult:
        return await self._inner.plan(request)

    async def analyze(self, request: AnalyzeRequest) -> AnalysisResult:
        return await self._inner.analyze(request)

    async def verify(self, request: VerifyRequest) -> VerificationResult:
        return await self._inner.verify(request)

    async def synthesize(self, request: SynthesizeRequest) -> SynthesisResult:
        from rca_agent.domain import Confidence, RecommendedActionType
        from rca_agent.llm.base import ProposedRootCause

        # If the graph feeds validation errors back, behave (so the repair path
        # can be exercised); otherwise, over-claim.
        if request.repair_errors:
            return await self._inner.synthesize(request)
        return SynthesisResult(
            conclusion="completed",
            summary="The database is definitely the cause.",
            root_cause=ProposedRootCause(
                statement="A database outage caused everything.",
                confidence=Confidence.HIGH,
                evidence_ids=["ev_999_fabricated"],
                reasoning_summary="trust me",
            ),
            recommended_action=ProposedAction(
                action_type=RecommendedActionType.RESTART_SERVICE,
                target_service="database",
                description="Restart the database.",
                rationale="It is broken.",
                evidence_ids=["ev_999_fabricated"],
            ),
            overall_confidence=Confidence.HIGH,
            uncertainty="none, I am certain",
        )
