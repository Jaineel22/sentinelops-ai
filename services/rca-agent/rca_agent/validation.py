"""Deterministic RCA validation boundary (ADR-019 / hallucination control).

Runs *after* the LLM (or mock) produces an :class:`~rca_agent.schemas.RCAReport`
and *before* it is persisted or returned:

    raw model output -> Pydantic parse -> validate_report() -> persist / return

``validate_report`` enforces the rules the schema alone cannot:

* every cited evidence id was actually collected this investigation
  (no invented evidence);
* a stated root cause is backed by >= 1 evidence id and only appears on a
  ``COMPLETED`` report;
* ``INSUFFICIENT_EVIDENCE`` reports carry no root cause;
* a finding claiming >= LOW confidence cites >= 1 evidence id
  (no evidence -> no factual claim);
* the report is not more confident than its own root cause;
* uncertainty is stated explicitly;
* the recommendation still requires human approval (belt-and-braces).
"""

from __future__ import annotations

from rca_agent.domain import Confidence, FindingType, InvestigationStatus
from rca_agent.schemas import RCAReport


class RcaValidationError(ValueError):
    """One or more deterministic RCA validation rules failed."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def referenced_evidence_ids(report: RCAReport) -> set[str]:
    """Every evidence id the report points at, from any section."""

    ids: set[str] = set()
    for finding in (*report.findings, *report.contributing_factors):
        ids.update(finding.evidence_ids)
    for hyp in report.hypotheses:
        ids.update(hyp.supporting_evidence_ids)
        ids.update(hyp.contradicting_evidence_ids)
    for entry in report.timeline:
        ids.update(entry.evidence_ids)
    ids.update(report.recommended_action.evidence_ids)
    if report.root_cause is not None:
        ids.update(report.root_cause.evidence_ids)
    return ids


def validate_evidence_references(report: RCAReport, known_ids: set[str]) -> list[str]:
    dangling = sorted(referenced_evidence_ids(report) - known_ids)
    return [f"references evidence id {i!r} which was not collected" for i in dangling]


def validate_consistency(report: RCAReport) -> list[str]:
    errors: list[str] = []

    if report.status is InvestigationStatus.INSUFFICIENT_EVIDENCE and report.root_cause is not None:
        errors.append("INSUFFICIENT_EVIDENCE report must not assert a root cause")

    if report.root_cause is not None:
        if not report.root_cause.evidence_ids:
            errors.append("root cause asserted with no supporting evidence")
        if report.status is not InvestigationStatus.COMPLETED:
            errors.append("root cause may only appear on a COMPLETED report")
        if report.overall_confidence.rank > report.root_cause.confidence.rank:
            errors.append("overall confidence exceeds root-cause confidence")

    for finding in (*report.findings, *report.contributing_factors):
        if finding.confidence.rank >= Confidence.LOW.rank and not finding.evidence_ids:
            errors.append(
                f"finding {finding.id!r} claims {finding.confidence} confidence with no evidence"
            )

    for factor in report.contributing_factors:
        if factor.type is not FindingType.CONTRIBUTING_FACTOR:
            errors.append(f"contributing factor {factor.id!r} has wrong type {factor.type}")

    if not report.uncertainty.strip():
        errors.append("uncertainty must be stated explicitly")

    if report.recommended_action.requires_human_approval is not True:
        errors.append("recommended action must require human approval")

    return errors


def validate_report(report: RCAReport, known_evidence_ids: set[str]) -> None:
    """Raise :class:`RcaValidationError` with every failure, or return ``None``."""

    errors = validate_evidence_references(report, known_evidence_ids)
    errors += validate_consistency(report)
    if errors:
        raise RcaValidationError(errors)
