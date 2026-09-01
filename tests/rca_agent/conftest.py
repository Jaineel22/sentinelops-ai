"""Fixtures for rca-agent unit tests (Sub-phases 4A + 4B).

No LLM, no Kafka, no PostgreSQL. ``sqlite_rca_db`` exercises the real SQLAlchemy
models against a throwaway file SQLite database. HTTP-backed 4B tools are tested
with an ``httpx.MockTransport`` — see ``tests/rca_agent/incident_api_fakes.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

from rca_agent.db import Database
from rca_agent.domain import (
    Confidence,
    EvidenceSourceType,
    FindingType,
    HypothesisVerdict,
    InvestigationStatus,
    RecommendedActionType,
    TrustLevel,
)
from rca_agent.limits import ResourceLimits
from rca_agent.schemas import (
    Evidence,
    Finding,
    Hypothesis,
    InvestigationMetadata,
    RCAReport,
    RecommendedAction,
    RootCause,
)
from rca_agent.tools.context import ToolContext

_BASE = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


def make_evidence(
    *,
    id: str = "ev_001",
    source_type: EvidenceSourceType = EvidenceSourceType.METRIC,
    service: str | None = "orders-service",
    trust_level: TrustLevel = TrustLevel.TRUSTED_SYSTEM,
    summary: str = "orders-service p95 latency rose to 780ms",
    content: dict[str, object] | None = None,
    tool_name: str = "get_service_metrics",
) -> Evidence:
    return Evidence(
        id=id,
        source_type=source_type,
        source_reference=f"metric:{service}/http_request_latency_p95",
        trust_level=trust_level,
        tool_name=tool_name,
        service=service,
        summary=summary,
        content=content or {"metric": "http_request_latency_p95_ms", "value": 780.0},
        observed_at=_BASE,
        collected_at=_BASE,
    )


def make_recommended_action(
    *,
    action_type: RecommendedActionType = RecommendedActionType.INVESTIGATE_FURTHER,
    evidence_ids: list[str] | None = None,
) -> RecommendedAction:
    return RecommendedAction(
        action_type=action_type,
        target_service="orders-service",
        description="Check the database connection pool saturation on orders-service.",
        rationale="Latency rose in lockstep with error rate; no deploy in the window.",
        evidence_ids=evidence_ids or ["ev_001"],
    )


def make_rca_report(
    *,
    incident_id: str = "inc_abc123",
    investigation_id: str = "rca_abc123",
    status: InvestigationStatus = InvestigationStatus.COMPLETED,
    evidence: list[Evidence] | None = None,
    findings: list[Finding] | None = None,
    hypotheses: list[Hypothesis] | None = None,
    root_cause: RootCause | None = None,
    contributing_factors: list[Finding] | None = None,
    overall_confidence: Confidence = Confidence.MEDIUM,
    uncertainty: str = "Database-side metrics are not available; inferred from timing correlation.",
) -> RCAReport:
    evidence = evidence or [make_evidence()]
    if root_cause is None and status is InvestigationStatus.COMPLETED:
        root_cause = RootCause(
            statement="Database latency spike saturated the orders-service connection pool.",
            confidence=Confidence.MEDIUM,
            evidence_ids=["ev_001"],
            reasoning_summary="Latency and error rate rose together; no deploy in the window.",
        )
    meta = InvestigationMetadata(
        mode="mock",
        llm_provider="mock",
        model=None,
        started_at=_BASE,
        completed_at=_BASE,
        duration_seconds=0.0,
        tool_call_count=len(evidence),
        step_count=len(evidence) + 2,
        evidence_count=len(evidence),
        termination_reason="analysis_complete",
        limits=ResourceLimits(),
    )
    return RCAReport(
        incident_id=incident_id,
        investigation_id=investigation_id,
        status=status,  # type: ignore[arg-type]
        summary="Checkout latency incident on orders-service.",
        severity="HIGH",
        affected_services=["orders-service"],
        findings=findings or [],
        hypotheses=hypotheses or [],
        root_cause=root_cause,
        contributing_factors=contributing_factors or [],
        recommended_action=make_recommended_action(),
        evidence=evidence,
        overall_confidence=overall_confidence,
        uncertainty=uncertainty,
        investigation_metadata=meta,
    )


@pytest.fixture
def evidence_factory() -> Callable[..., Evidence]:
    return make_evidence


@pytest.fixture
def rca_report_factory() -> Callable[..., RCAReport]:
    return make_rca_report


@pytest.fixture
def finding_factory() -> Callable[..., Finding]:
    def _make(
        *,
        id: str = "fi_001",
        type: FindingType = FindingType.OBSERVATION,
        statement: str = "p95 latency exceeded 500ms for 4 consecutive windows",
        evidence_ids: list[str] | None = None,
        confidence: Confidence = Confidence.MEDIUM,
    ) -> Finding:
        return Finding(
            id=id,
            type=type,
            statement=statement,
            evidence_ids=evidence_ids if evidence_ids is not None else ["ev_001"],
            confidence=confidence,
        )

    return _make


@pytest.fixture
def hypothesis_factory() -> Callable[..., Hypothesis]:
    def _make(
        *,
        id: str = "hy_001",
        statement: str = "A database latency spike is the trigger.",
        supporting_evidence_ids: list[str] | None = None,
        contradicting_evidence_ids: list[str] | None = None,
        verdict: HypothesisVerdict = HypothesisVerdict.SUPPORTED,
    ) -> Hypothesis:
        return Hypothesis(
            id=id,
            statement=statement,
            supporting_evidence_ids=supporting_evidence_ids
            if supporting_evidence_ids is not None
            else ["ev_001"],
            contradicting_evidence_ids=contradicting_evidence_ids or [],
            assessment="Timing lines up with the incident window.",
            verdict=verdict,
        )

    return _make


@pytest_asyncio.fixture
async def sqlite_rca_db(tmp_path: Path) -> AsyncIterator[Database]:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'rca.db'}")
    await db.create_all()
    try:
        yield db
    finally:
        await db.dispose()


# --- 4B: evidence-tool fixtures -----------------------------------------
@pytest.fixture
def tool_context() -> ToolContext:
    return ToolContext(max_evidence_items=40, now_fn=lambda: _BASE)


@pytest.fixture
def small_budget_context() -> ToolContext:
    return ToolContext(max_evidence_items=1, now_fn=lambda: _BASE)
