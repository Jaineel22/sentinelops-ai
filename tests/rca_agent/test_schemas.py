"""Phase 4 typed contracts (ADR-019)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from rca_agent.domain import (
    Confidence,
    EvidenceSourceType,
    InvestigationStatus,
    RecommendedActionType,
)
from rca_agent.schemas import (
    AVAILABLE_EVIDENCE_SOURCES,
    UNAVAILABLE_EVIDENCE_SOURCES,
    Evidence,
    RCAReport,
    RecommendedAction,
)


def test_confidence_rank_is_ordered() -> None:
    order = [Confidence.UNKNOWN, Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH]
    assert [c.rank for c in order] == [0, 1, 2, 3]
    assert Confidence.HIGH.rank > Confidence.LOW.rank


def test_evidence_is_frozen(evidence_factory: Callable[..., Evidence]) -> None:
    ev = evidence_factory()
    with pytest.raises(ValidationError):
        ev.summary = "tampered"


def test_evidence_id_format_is_enforced(evidence_factory: Callable[..., Evidence]) -> None:
    evidence_factory(id="ev_001")  # ok
    for bad in ("001", "EV_1", "ev-1", "evidence_1", "ev_1!"):
        with pytest.raises(ValidationError):
            evidence_factory(id=bad)


def test_recommended_action_cannot_waive_human_approval() -> None:
    with pytest.raises(ValidationError):
        RecommendedAction(
            action_type=RecommendedActionType.RESTART_SERVICE,
            description="x",
            rationale="y",
            requires_human_approval=False,  # type: ignore[arg-type]
        )
    ra = RecommendedAction(
        action_type=RecommendedActionType.RESTART_SERVICE, description="x", rationale="y"
    )
    assert ra.requires_human_approval is True


def test_recommended_action_type_is_a_closed_set() -> None:
    with pytest.raises(ValidationError):
        RecommendedAction(
            action_type="rm -rf /",  # type: ignore[arg-type]
            description="x",
            rationale="y",
        )


def test_rca_report_status_is_restricted_to_analytical_outcomes(
    rca_report_factory: Callable[..., RCAReport],
) -> None:
    rca_report_factory(status=InvestigationStatus.COMPLETED)
    rca_report_factory(status=InvestigationStatus.INSUFFICIENT_EVIDENCE, root_cause=None)
    with pytest.raises(ValidationError):
        RCAReport.model_validate(
            {
                **rca_report_factory().model_dump(mode="json"),
                "status": "FAILED",
            }
        )


def test_rca_report_round_trips_through_json(
    rca_report_factory: Callable[..., RCAReport],
) -> None:
    report = rca_report_factory()
    restored = RCAReport.model_validate_json(report.model_dump_json())
    assert restored == report
    assert restored.root_cause is not None
    assert restored.recommended_action.requires_human_approval is True


def test_rca_report_allows_undetermined_root_cause(
    rca_report_factory: Callable[..., RCAReport],
) -> None:
    report = rca_report_factory(status=InvestigationStatus.INSUFFICIENT_EVIDENCE, root_cause=None)
    assert report.root_cause is None


def test_evidence_source_availability_partitions_the_enum() -> None:
    assert AVAILABLE_EVIDENCE_SOURCES.isdisjoint(UNAVAILABLE_EVIDENCE_SOURCES)
    assert set(EvidenceSourceType) == AVAILABLE_EVIDENCE_SOURCES | UNAVAILABLE_EVIDENCE_SOURCES
    # Sources with no backend in this repo today are honestly marked unavailable.
    assert EvidenceSourceType.LOG in UNAVAILABLE_EVIDENCE_SOURCES
    assert EvidenceSourceType.TRACE in UNAVAILABLE_EVIDENCE_SOURCES
    assert EvidenceSourceType.DEPLOYMENT in UNAVAILABLE_EVIDENCE_SOURCES
    assert EvidenceSourceType.METRIC in AVAILABLE_EVIDENCE_SOURCES
    assert EvidenceSourceType.INCIDENT in AVAILABLE_EVIDENCE_SOURCES
