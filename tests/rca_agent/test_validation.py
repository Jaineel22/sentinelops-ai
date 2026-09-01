"""Deterministic RCA validation boundary (hallucination control, ADR-019)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from rca_agent.domain import Confidence, FindingType, InvestigationStatus
from rca_agent.schemas import Finding, Hypothesis, RCAReport, RootCause
from rca_agent.validation import (
    RcaValidationError,
    referenced_evidence_ids,
    validate_report,
)


def _known(report: RCAReport) -> set[str]:
    return {e.id for e in report.evidence}


def test_happy_path_validates(rca_report_factory: Callable[..., RCAReport]) -> None:
    report = rca_report_factory()
    validate_report(report, _known(report))


def test_dangling_evidence_reference_is_rejected(
    rca_report_factory: Callable[..., RCAReport],
    finding_factory: Callable[..., Finding],
) -> None:
    report = rca_report_factory(
        findings=[finding_factory(id="fi_x", evidence_ids=["ev_001", "ev_ghost"])]
    )
    with pytest.raises(RcaValidationError) as exc:
        validate_report(report, _known(report))
    assert any("ev_ghost" in e for e in exc.value.errors)


def test_root_cause_without_evidence_is_rejected(
    rca_report_factory: Callable[..., RCAReport],
) -> None:
    report = rca_report_factory(
        root_cause=RootCause(
            statement="It was the database.",
            confidence=Confidence.MEDIUM,
            evidence_ids=[],
            reasoning_summary="hunch",
        )
    )
    with pytest.raises(RcaValidationError) as exc:
        validate_report(report, _known(report))
    assert any("no supporting evidence" in e for e in exc.value.errors)


def test_insufficient_evidence_report_must_not_assert_root_cause(
    rca_report_factory: Callable[..., RCAReport],
) -> None:
    report = rca_report_factory(
        status=InvestigationStatus.INSUFFICIENT_EVIDENCE,
        root_cause=RootCause(
            statement="db",
            confidence=Confidence.LOW,
            evidence_ids=["ev_001"],
            reasoning_summary="x",
        ),
    )
    with pytest.raises(RcaValidationError):
        validate_report(report, _known(report))


def test_confident_finding_without_evidence_is_rejected(
    rca_report_factory: Callable[..., RCAReport],
    finding_factory: Callable[..., Finding],
) -> None:
    report = rca_report_factory(
        findings=[finding_factory(id="fi_c", confidence=Confidence.HIGH, evidence_ids=[])]
    )
    with pytest.raises(RcaValidationError) as exc:
        validate_report(report, _known(report))
    assert any("no evidence" in e for e in exc.value.errors)


def test_unknown_finding_with_no_evidence_is_allowed(
    rca_report_factory: Callable[..., RCAReport],
    finding_factory: Callable[..., Finding],
) -> None:
    # A hypothesis-level finding with UNKNOWN confidence and no evidence is fine.
    report = rca_report_factory(
        findings=[
            finding_factory(
                id="fi_u",
                type=FindingType.HYPOTHESIS,
                confidence=Confidence.UNKNOWN,
                evidence_ids=[],
            )
        ]
    )
    validate_report(report, _known(report))


def test_overall_confidence_may_not_exceed_root_cause_confidence(
    rca_report_factory: Callable[..., RCAReport],
) -> None:
    report = rca_report_factory(
        overall_confidence=Confidence.HIGH,
        root_cause=RootCause(
            statement="db latency",
            confidence=Confidence.LOW,
            evidence_ids=["ev_001"],
            reasoning_summary="weak",
        ),
    )
    with pytest.raises(RcaValidationError) as exc:
        validate_report(report, _known(report))
    assert any("overall confidence exceeds" in e for e in exc.value.errors)


def test_empty_uncertainty_is_rejected(
    rca_report_factory: Callable[..., RCAReport],
) -> None:
    report = rca_report_factory(uncertainty="   ")
    with pytest.raises(RcaValidationError) as exc:
        validate_report(report, _known(report))
    assert any("uncertainty" in e for e in exc.value.errors)


def test_contributing_factor_with_wrong_type_is_rejected(
    rca_report_factory: Callable[..., RCAReport],
    finding_factory: Callable[..., Finding],
) -> None:
    report = rca_report_factory(
        contributing_factors=[finding_factory(id="fi_bad", type=FindingType.OBSERVATION)]
    )
    with pytest.raises(RcaValidationError) as exc:
        validate_report(report, _known(report))
    assert any("wrong type" in e for e in exc.value.errors)


def test_referenced_evidence_ids_gathers_every_section(
    rca_report_factory: Callable[..., RCAReport],
    finding_factory: Callable[..., Finding],
    hypothesis_factory: Callable[..., Hypothesis],
) -> None:
    report = rca_report_factory(
        findings=[finding_factory(id="fi_1", evidence_ids=["ev_001"])],
        hypotheses=[hypothesis_factory(id="hy_1", supporting_evidence_ids=["ev_001"])],
    )
    assert "ev_001" in referenced_evidence_ids(report)
