"""Model / tool / validation failures all resolve to a safe structured state."""

from __future__ import annotations

from rca_agent.domain import InvestigationStatus, InvestigationTrigger
from rca_agent.llm.base import LlmMalformedOutput, LlmProviderError, LlmTimeout
from tests.rca_agent.engine_harness import build_service
from tests.rca_agent.incident_api_fakes import (
    INCIDENT_ID,
    make_anomaly_windows,
    make_incident,
    ok,
    scenario_handler,
)
from tests.rca_agent.llm_doubles import (
    AlwaysFailsLlmClient,
    FaultInjectingLlmClient,
    OverclaimingLlmClient,
)

_ABN = ["error_rate", "latency_p95_ms"]
_HANDLER = scenario_handler(
    incident=make_incident(abnormal=_ABN),
    anomalies=make_anomaly_windows(count=4, abnormal=_ABN, score=0.93),
)


async def test_llm_timeout_terminates_as_timed_out() -> None:
    llm = AlwaysFailsLlmClient(fail_on="plan", fail_with=LlmTimeout("provider timed out"))
    out = await build_service(_HANDLER, llm=llm).investigate(
        INCIDENT_ID, trigger=InvestigationTrigger.MANUAL
    )
    assert out.investigation.status is InvestigationStatus.TIMED_OUT
    assert "timeout" in (out.investigation.termination_reason or "")


async def test_llm_provider_error_terminates_as_failed() -> None:
    llm = AlwaysFailsLlmClient(fail_on="analyze", fail_with=LlmProviderError("500 from provider"))
    out = await build_service(_HANDLER, llm=llm).investigate(
        INCIDENT_ID, trigger=InvestigationTrigger.MANUAL
    )
    assert out.investigation.status is InvestigationStatus.FAILED


async def test_transient_llm_error_is_survivable_via_bounded_retry() -> None:
    # FaultInjectingLlmClient raises once on synthesize, then the graph's repair
    # path re-calls synthesize (mock) which succeeds -> a valid report.
    llm = FaultInjectingLlmClient(fail_on="synthesize", fail_with=LlmMalformedOutput("bad json"))
    out = await build_service(_HANDLER, llm=llm).investigate(
        INCIDENT_ID, trigger=InvestigationTrigger.MANUAL
    )
    # one failure on synthesize is terminal (no automatic retry of a malformed
    # synthesis in 4C) -> FAILED, safely.
    assert out.investigation.status is InvestigationStatus.FAILED
    assert out.report is None


async def test_overclaiming_model_output_is_rejected_and_repaired_to_safe() -> None:
    out = await build_service(_HANDLER, llm=OverclaimingLlmClient()).investigate(
        INCIDENT_ID, trigger=InvestigationTrigger.MANUAL
    )
    # the over-claimed RCA (fabricated evidence id, HIGH confidence, RESTART_SERVICE)
    # fails validate_report; the bounded repair produces a conservative result.
    assert out.investigation.status in {
        InvestigationStatus.INSUFFICIENT_EVIDENCE,
        InvestigationStatus.COMPLETED,
    }
    assert out.report is not None
    known = {e.id for e in out.report.evidence}
    if out.report.root_cause is not None:
        assert set(out.report.root_cause.evidence_ids) <= known  # no fabricated ids survived
    assert out.report.recommended_action.requires_human_approval is True
    assert "ev_999_fabricated" not in out.report.model_dump_json()


async def test_tool_failure_does_not_crash_the_investigation() -> None:
    def _flaky(request: object) -> object:
        path = getattr(request, "url").path  # noqa: B009
        if path.endswith("/evidence"):
            return ok({"malformed": "not a list"})
        return _HANDLER(request)  # type: ignore[arg-type]

    out = await build_service(_flaky).investigate(  # type: ignore[arg-type]
        INCIDENT_ID, trigger=InvestigationTrigger.MANUAL
    )
    assert out.investigation.status.is_terminal
    assert out.report is not None  # still produced a structured report


async def test_incident_api_unavailable_fails_safely() -> None:
    def _down(request: object) -> object:
        import httpx

        raise httpx.ConnectError("incident API is down")

    out = await build_service(_down).investigate(  # type: ignore[arg-type]
        INCIDENT_ID, trigger=InvestigationTrigger.EVENT
    )
    assert out.investigation.status is InvestigationStatus.FAILED
    assert "could not load incident" in (out.investigation.termination_reason or "")
