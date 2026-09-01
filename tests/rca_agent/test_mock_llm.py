"""The deterministic mock reasoner (ADR-021)."""

from __future__ import annotations

from collections.abc import Callable

from rca_agent.domain import Confidence, HypothesisVerdict
from rca_agent.llm.base import (
    AnalyzeRequest,
    PlanRequest,
    ProposedHypothesis,
    SynthesizeRequest,
    VerifyRequest,
)
from rca_agent.llm.mock import MockLlmClient
from rca_agent.schemas import Evidence, Hypothesis

_INCIDENT = {"id": "inc_00112233", "service": "orders-service", "severity": "HIGH"}


def _anomaly(ev_id: str, factory: Callable[..., Evidence]) -> Evidence:
    from rca_agent.domain import EvidenceSourceType

    return factory(
        id=ev_id,
        source_type=EvidenceSourceType.ANOMALY,
        content={
            "detector": "isolation_forest",
            "anomaly_score": 0.9,
            "threshold": 0.5,
            "abnormal_signals": ["error_rate"],
            "window_start": "2026-09-01T12:00:00+00:00",
        },
    )


async def test_plan_is_deterministic_and_only_registered_tools(
    evidence_factory: Callable[..., Evidence],
) -> None:
    mock = MockLlmClient()
    r1 = await mock.plan(PlanRequest(incident=_INCIDENT, evidence=[]))
    r2 = await mock.plan(PlanRequest(incident=_INCIDENT, evidence=[]))
    assert [c.tool for c in r1.calls] == [c.tool for c in r2.calls]
    assert {c.tool for c in r1.calls} <= {
        "get_anomaly_evidence",
        "get_incident_timeline",
        "get_related_incidents",
        "get_service_metrics",
        "get_service_health",
    }


async def test_analyze_grounds_findings_in_evidence(
    evidence_factory: Callable[..., Evidence],
) -> None:
    ev = [_anomaly("ev_001", evidence_factory), _anomaly("ev_002", evidence_factory)]
    result = await MockLlmClient().analyze(AnalyzeRequest(incident=_INCIDENT, evidence=ev))
    assert result.findings
    known = {e.id for e in ev}
    for f in result.findings:
        assert set(f.evidence_ids) <= known
    assert result.hypotheses
    for h in result.hypotheses:
        assert set(h.supporting_evidence_ids) <= known


async def test_verify_assigns_verdicts_by_evidence_count() -> None:
    mock = MockLlmClient()
    strong = ProposedHypothesis(statement="x", supporting_evidence_ids=["ev_001", "ev_002"])
    weak = ProposedHypothesis(statement="y", supporting_evidence_ids=["ev_001"])
    conflicted = ProposedHypothesis(
        statement="z", supporting_evidence_ids=["ev_001"], contradicting_evidence_ids=["ev_002"]
    )
    result = await mock.verify(
        VerifyRequest(incident=_INCIDENT, hypotheses=[strong, weak, conflicted])
    )
    verdicts = {v.index: v.verdict for v in result.verdicts}
    assert verdicts[0] is HypothesisVerdict.SUPPORTED
    assert verdicts[1] is HypothesisVerdict.UNVERIFIED
    assert verdicts[2] is HypothesisVerdict.CONFLICTING


async def test_synthesize_insufficient_when_no_anomaly_evidence() -> None:
    result = await MockLlmClient().synthesize(
        SynthesizeRequest(incident=_INCIDENT, investigation_id="rca_1", evidence=[])
    )
    assert result.conclusion == "insufficient_evidence"
    assert result.root_cause is None
    assert result.uncertainty.strip()
    assert result.recommended_action.action_type


async def test_synthesize_supported_root_cause_when_strong_evidence(
    evidence_factory: Callable[..., Evidence],
) -> None:
    ev = [_anomaly(f"ev_{i:03d}", evidence_factory) for i in range(1, 4)]
    hyp = Hypothesis(
        id="hy_001",
        statement="Abnormal error_rate is the driver.",
        supporting_evidence_ids=[e.id for e in ev],
        assessment="strong",
        verdict=HypothesisVerdict.SUPPORTED,
    )
    result = await MockLlmClient().synthesize(
        SynthesizeRequest(
            incident=_INCIDENT, investigation_id="rca_1", evidence=ev, hypotheses=[hyp]
        )
    )
    assert result.conclusion == "completed"
    assert result.root_cause is not None
    assert result.root_cause.confidence is Confidence.MEDIUM
    assert set(result.root_cause.evidence_ids) <= {e.id for e in ev}


async def test_synthesize_conservative_on_repair(
    evidence_factory: Callable[..., Evidence],
) -> None:
    ev = [_anomaly(f"ev_{i:03d}", evidence_factory) for i in range(1, 4)]
    result = await MockLlmClient().synthesize(
        SynthesizeRequest(
            incident=_INCIDENT,
            investigation_id="rca_1",
            evidence=ev,
            repair_errors=["references evidence id 'ev_999' which was not collected"],
        )
    )
    assert result.conclusion == "insufficient_evidence"
    assert result.root_cause is None
