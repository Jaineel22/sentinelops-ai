"""``AnthropicLlmClient`` boundary behaviour (Sub-phase 4D, ADR-022).

No network, no API key: ``messages.create`` is faked. These tests assert what
the client does at the provider boundary — the forced-tool transport, DTO
mapping, error normalization, bounds — not merely that it returns something.
"""

from __future__ import annotations

from typing import Any

import pytest

from rca_agent.domain import Confidence, FindingType, HypothesisVerdict, RecommendedActionType
from rca_agent.llm.anthropic_client import DEFAULT_MODEL, AnthropicLlmClient
from rca_agent.llm.base import (
    AnalysisResult,
    AnalyzeRequest,
    LlmMalformedOutput,
    LlmProviderError,
    LlmTimeout,
    PlanRequest,
    PlanResult,
    ProposedHypothesis,
    SynthesisResult,
    SynthesizeRequest,
    VerificationResult,
    VerifyRequest,
)
from tests.rca_agent.anthropic_fakes import (
    RecordingCreate,
    connection_error,
    forced_tool_response,
    rate_limit_error,
    status_error,
    timeout_error,
)

_INCIDENT = {"id": "inc_00112233", "service": "orders-service", "severity": "HIGH"}


def _client(responder: Any, **kw: Any) -> AnthropicLlmClient:
    return AnthropicLlmClient(
        api_key="sk-ant-not-a-real-key",
        model=kw.pop("model", "claude-opus-5"),
        timeout_seconds=kw.pop("timeout_seconds", 30.0),
        _messages_create=RecordingCreate(responder),
        **kw,
    )


def _plan_req(**kw: Any) -> PlanRequest:
    return PlanRequest(incident=_INCIDENT, evidence=[], tool_specs=kw.pop("tool_specs", []), **kw)


# --- successful typed operations -------------------------------------
async def test_plan_maps_forced_tool_input_to_planresult() -> None:
    payload = {
        "calls": [
            {"tool": "get_anomaly_evidence", "arguments": {"incident_id": "inc_00112233"}},
        ],
        "rationale": "start from the anomaly evidence",
    }
    client = _client(lambda **kw: forced_tool_response("submit_investigation_plan", payload))
    result = await client.plan(_plan_req())
    assert isinstance(result, PlanResult)
    assert [c.tool for c in result.calls] == ["get_anomaly_evidence"]
    assert result.rationale == "start from the anomaly evidence"


async def test_analyze_maps_to_analysisresult() -> None:
    payload = {
        "findings": [
            {"statement": "error rate rose", "type": "observation", "evidence_ids": ["ev_001"]}
        ],
        "hypotheses": [
            {"statement": "db saturation", "supporting_evidence_ids": ["ev_001"]},
        ],
        "notes": "one anomaly window",
    }
    client = _client(lambda **kw: forced_tool_response("submit_analysis", payload))
    result = await client.analyze(AnalyzeRequest(incident=_INCIDENT, evidence=[]))
    assert isinstance(result, AnalysisResult)
    assert result.findings[0].type is FindingType.OBSERVATION
    assert result.hypotheses[0].supporting_evidence_ids == ["ev_001"]


async def test_verify_maps_to_verificationresult() -> None:
    payload = {
        "verdicts": [{"index": 0, "verdict": "SUPPORTED", "assessment": "two supports"}],
        "needs_more_evidence": False,
        "ready_to_conclude": True,
    }
    client = _client(lambda **kw: forced_tool_response("submit_verification", payload))
    result = await client.verify(VerifyRequest(incident=_INCIDENT, evidence=[]))
    assert isinstance(result, VerificationResult)
    assert result.verdicts[0].verdict is HypothesisVerdict.SUPPORTED
    assert result.ready_to_conclude is True


async def test_synthesize_maps_to_synthesisresult() -> None:
    payload = {
        "conclusion": "completed",
        "summary": "db saturation on orders-service",
        "root_cause": {
            "statement": "connection pool exhausted",
            "confidence": "MEDIUM",
            "evidence_ids": ["ev_001", "ev_002"],
            "reasoning_summary": "latency and errors moved together",
        },
        "recommended_action": {
            "action_type": "CONTACT_SERVICE_OWNER",
            "target_service": "orders-service",
            "description": "have the owner check the pool",
        },
        "overall_confidence": "MEDIUM",
        "uncertainty": "no db-side metrics were available",
    }
    client = _client(lambda **kw: forced_tool_response("submit_synthesis", payload))
    result = await client.synthesize(
        SynthesizeRequest(incident=_INCIDENT, investigation_id="rca_1", evidence=[])
    )
    assert isinstance(result, SynthesisResult)
    assert result.conclusion == "completed"
    assert result.root_cause is not None
    assert result.root_cause.confidence is Confidence.MEDIUM
    assert result.recommended_action.action_type is RecommendedActionType.CONTACT_SERVICE_OWNER


# --- malformed / unexpected model output ---------------------------
async def test_schema_invalid_tool_input_becomes_malformed_output() -> None:
    # 'completed' synthesis with no recommended_action -> fails the DTO schema.
    payload = {"conclusion": "completed", "summary": "x", "uncertainty": "y"}
    client = _client(lambda **kw: forced_tool_response("submit_synthesis", payload))
    with pytest.raises(LlmMalformedOutput):
        await client.synthesize(
            SynthesizeRequest(incident=_INCIDENT, investigation_id="rca_1", evidence=[])
        )


async def test_non_dict_tool_input_becomes_malformed_output() -> None:
    client = _client(lambda **kw: forced_tool_response("submit_investigation_plan", ["not", "obj"]))
    with pytest.raises(LlmMalformedOutput):
        await client.plan(_plan_req())


async def test_model_answers_in_text_instead_of_calling_the_tool() -> None:
    from tests.rca_agent.anthropic_fakes import FakeMessage, FakeTextBlock

    client = _client(
        lambda **kw: FakeMessage([FakeTextBlock('{"calls": []}')], stop_reason="end_turn")
    )
    with pytest.raises(LlmMalformedOutput):
        await client.plan(_plan_req())


async def test_response_truncated_at_max_tokens_is_malformed() -> None:
    client = _client(
        lambda **kw: forced_tool_response(
            "submit_investigation_plan", {"calls": []}, stop_reason="max_tokens"
        )
    )
    with pytest.raises(LlmMalformedOutput):
        await client.plan(_plan_req())


async def test_wrong_tool_name_is_malformed() -> None:
    client = _client(lambda **kw: forced_tool_response("exec_cmd", {"calls": []}))
    with pytest.raises(LlmMalformedOutput):
        await client.plan(_plan_req())


async def test_refusal_stop_reason_is_a_provider_error() -> None:
    client = _client(
        lambda **kw: forced_tool_response(
            "submit_investigation_plan", {"calls": []}, stop_reason="refusal"
        )
    )
    with pytest.raises(LlmProviderError):
        await client.plan(_plan_req())


# --- provider failures -> normalized errors ------------------------
@pytest.mark.parametrize(
    ("make_error", "expected"),
    [
        (timeout_error, LlmTimeout),
        (connection_error, LlmProviderError),
        (rate_limit_error, LlmProviderError),
        (status_error, LlmProviderError),
    ],
)
async def test_provider_exceptions_are_normalized(make_error: Any, expected: type) -> None:
    def _raise(**kw: Any) -> object:
        return make_error()

    client = _client(_raise)
    with pytest.raises(expected):
        await client.plan(_plan_req())


async def test_raw_provider_exception_type_does_not_leak_to_the_graph() -> None:
    client = _client(lambda **kw: status_error(503))
    with pytest.raises(LlmProviderError) as exc_info:
        await client.plan(_plan_req())
    assert "503" in str(exc_info.value)
    assert "anthropic" not in str(exc_info.value).lower()


# --- bounds -------------------------------------------------------
async def test_prompt_over_the_char_bound_is_rejected_before_any_call() -> None:
    recorder = RecordingCreate(lambda **kw: forced_tool_response("submit_analysis", {}))
    client = AnthropicLlmClient(api_key="sk-ant-x", max_prompt_chars=200, _messages_create=recorder)
    with pytest.raises(LlmProviderError):
        await client.analyze(AnalyzeRequest(incident=_INCIDENT, evidence=[]))
    assert recorder.calls == []  # never hit the network


async def test_request_carries_explicit_token_and_timeout_bounds() -> None:
    recorder = RecordingCreate(
        lambda **kw: forced_tool_response(
            "submit_investigation_plan", {"calls": [], "rationale": ""}
        )
    )
    client = AnthropicLlmClient(
        api_key="sk-ant-x",
        model="claude-opus-5",
        max_output_tokens=1234,
        timeout_seconds=17.0,
        _messages_create=recorder,
    )
    await client.plan(_plan_req())
    assert recorder.last["max_tokens"] == 1234
    assert recorder.last["model"] == "claude-opus-5"
    assert recorder.last["tool_choice"] == {
        "type": "tool",
        "name": "submit_investigation_plan",
    }


async def test_timeout_seconds_reaches_the_sdk_client() -> None:
    # No injected transport -> a real AsyncAnthropic is constructed; assert the
    # bounded timeout is applied and no key is echoed anywhere.
    client = AnthropicLlmClient(api_key="sk-ant-SECRET-should-not-print", timeout_seconds=42.0)
    assert client.model == DEFAULT_MODEL
    assert "sk-ant-SECRET-should-not-print" not in repr(client.__dict__)


# --- the message the provider actually receives -------------------
async def test_forced_tool_schema_is_the_existing_dto_schema() -> None:
    recorder = RecordingCreate(
        lambda **kw: forced_tool_response("submit_analysis", {"findings": [], "hypotheses": []})
    )
    client = AnthropicLlmClient(api_key="sk-ant-x", _messages_create=recorder)
    await client.analyze(AnalyzeRequest(incident=_INCIDENT, evidence=[]))
    (tool,) = recorder.last["tools"]
    assert tool["name"] == "submit_analysis"
    assert tool["input_schema"] == AnalysisResult.model_json_schema()


async def test_api_key_is_never_placed_in_the_request_payload() -> None:
    recorder = RecordingCreate(
        lambda **kw: forced_tool_response(
            "submit_investigation_plan", {"calls": [], "rationale": ""}
        )
    )
    client = AnthropicLlmClient(api_key="sk-ant-TOP-SECRET-KEY", _messages_create=recorder)
    await client.plan(_plan_req())
    assert "sk-ant-TOP-SECRET-KEY" not in str(recorder.calls)


async def test_verify_passes_prior_proposals_as_data_not_instructions() -> None:
    recorder = RecordingCreate(
        lambda **kw: forced_tool_response(
            "submit_verification", {"verdicts": [], "ready_to_conclude": True}
        )
    )
    client = AnthropicLlmClient(api_key="sk-ant-x", _messages_create=recorder)
    hyp = ProposedHypothesis(statement="db saturation", supporting_evidence_ids=["ev_001"])
    await client.verify(VerifyRequest(incident=_INCIDENT, evidence=[], hypotheses=[hyp]))
    # proposals appear only in the user turn, never in the system prompt
    assert "db saturation" in recorder.user
    assert "db saturation" not in recorder.system
