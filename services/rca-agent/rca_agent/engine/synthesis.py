"""Deterministic assembly of the final :class:`RCAReport` (ADR-021).

The model produces a :class:`SynthesisResult`; this module turns it into a
schema-valid report: it assigns finding ids, drops any evidence reference the
model invented, clamps confidence so the report can never out-claim its own
root cause, fills ``unavailable_evidence_sources`` from the registry (not the
model), and stamps deterministic ``investigation_metadata``.
"""

from __future__ import annotations

from datetime import datetime

from rca_agent.domain import (
    Confidence,
    FindingType,
    InvestigationStatus,
    RecommendedActionType,
)
from rca_agent.engine.deps import GraphDeps
from rca_agent.llm.base import ProposedFinding, SynthesisResult
from rca_agent.schemas import (
    Evidence,
    Finding,
    Hypothesis,
    InvestigationMetadata,
    RCAReport,
    RecommendedAction,
    RootCause,
    TimelineEntry,
)
from rca_agent.tools.names import ToolAvailability


def _keep_ids(ids: list[str], known: set[str]) -> list[str]:
    return [i for i in ids if i in known]


def _finding(
    seq: int, pf: ProposedFinding, known: set[str], *, force_type: FindingType | None
) -> Finding:
    kept = _keep_ids(list(pf.evidence_ids), known)
    conf = pf.confidence if kept else Confidence.UNKNOWN
    return Finding(
        id=f"fi_{seq:03d}",
        type=force_type or pf.type,
        statement=pf.statement,
        evidence_ids=kept,
        confidence=conf,
    )


def unavailable_sources(deps: GraphDeps) -> list[str]:
    return [
        f"{t.name}: {t.description}"
        for t in deps.registry.unavailable()
        if t.availability is ToolAvailability.UNAVAILABLE
    ]


def build_metadata(
    deps: GraphDeps, *, started_at: datetime, evidence_count: int, termination_reason: str
) -> InvestigationMetadata:
    now = deps.now()
    return InvestigationMetadata(
        mode="mock" if deps.mode == "mock" else "live",
        llm_provider=deps.llm.provider,
        model=deps.llm.model,
        started_at=started_at,
        completed_at=now,
        duration_seconds=max((now - started_at).total_seconds(), 0.0),
        tool_call_count=deps.usage.tool_calls,
        step_count=deps.usage.steps,
        evidence_count=evidence_count,
        termination_reason=termination_reason[:200],
        limits=deps.limits,
    )


def build_rca_report(
    deps: GraphDeps,
    *,
    incident_id: str,
    investigation_id: str,
    incident: dict[str, object],
    evidence: list[Evidence],
    proposed_findings: list[ProposedFinding],
    hypotheses: list[Hypothesis],
    synthesis: SynthesisResult,
    started_at: datetime,
) -> RCAReport:
    known = {e.id for e in evidence}
    status = (
        InvestigationStatus.COMPLETED
        if synthesis.conclusion == "completed"
        else InvestigationStatus.INSUFFICIENT_EVIDENCE
    )
    insufficient = status is InvestigationStatus.INSUFFICIENT_EVIDENCE

    findings: list[Finding] = []
    contributing: list[Finding] = []
    _cf = FindingType.CONTRIBUTING_FACTOR
    for pf in proposed_findings:
        seq = len(findings) + len(contributing) + 1
        if pf.type is _cf:
            contributing.append(_finding(seq, pf, known, force_type=_cf))
        else:
            findings.append(_finding(seq, pf, known, force_type=None))
    for pf in synthesis.contributing_factors:
        seq = len(findings) + len(contributing) + 1
        contributing.append(_finding(seq, pf, known, force_type=_cf))

    root_cause: RootCause | None = None
    if synthesis.root_cause is not None and not insufficient:
        rc_ids = _keep_ids(list(synthesis.root_cause.evidence_ids), known)
        if rc_ids:
            root_cause = RootCause(
                statement=synthesis.root_cause.statement,
                confidence=synthesis.root_cause.confidence,
                evidence_ids=rc_ids,
                reasoning_summary=synthesis.root_cause.reasoning_summary or "See findings.",
            )

    overall = synthesis.overall_confidence
    if root_cause is not None and overall.rank > root_cause.confidence.rank:
        overall = root_cause.confidence
    if root_cause is None and overall.rank > Confidence.LOW.rank:
        overall = Confidence.LOW

    action = synthesis.recommended_action
    recommended = RecommendedAction(
        action_type=action.action_type,
        target_service=action.target_service,
        description=action.description or "Refer this incident to the service owner for review.",
        rationale=action.rationale or "Based on the collected evidence.",
        evidence_ids=_keep_ids(list(action.evidence_ids), known),
    )

    timeline = [
        TimelineEntry(
            at=t.at,
            description=t.description,
            evidence_ids=_keep_ids(list(t.evidence_ids), known),
        )
        for t in synthesis.timeline
    ]

    services = {e.service for e in evidence if e.service}
    if incident.get("service"):
        services.add(str(incident["service"]))

    return RCAReport(
        incident_id=incident_id,
        investigation_id=investigation_id,
        status=status,  # type: ignore[arg-type]
        summary=synthesis.summary or "Investigation summary unavailable.",
        severity=str(incident.get("severity")) if incident.get("severity") else None,
        affected_services=sorted(services),
        timeline=timeline,
        findings=findings,
        hypotheses=hypotheses,
        root_cause=root_cause,
        contributing_factors=contributing,
        recommended_action=recommended,
        evidence=evidence,
        overall_confidence=overall,
        uncertainty=synthesis.uncertainty or "Uncertainty was not characterized.",
        unavailable_evidence_sources=unavailable_sources(deps),
        investigation_metadata=build_metadata(
            deps,
            started_at=started_at,
            evidence_count=len(evidence),
            termination_reason="analysis_complete"
            if status is InvestigationStatus.COMPLETED
            else "insufficient_evidence",
        ),
    )


def build_fallback_report(
    deps: GraphDeps,
    *,
    incident_id: str,
    investigation_id: str,
    incident: dict[str, object],
    evidence: list[Evidence],
    hypotheses: list[Hypothesis],
    errors: list[str],
    started_at: datetime,
) -> RCAReport:
    """A guaranteed-valid INSUFFICIENT_EVIDENCE report, used when the model's
    synthesis fails deterministic validation after the bounded repair attempt."""

    anchor = [evidence[0].id] if evidence else []
    service = str(incident.get("service") or "unknown-service")
    return RCAReport(
        incident_id=incident_id,
        investigation_id=investigation_id,
        status=InvestigationStatus.INSUFFICIENT_EVIDENCE,
        summary=(
            f"Automated investigation of the {service} incident did not produce a "
            "validated root-cause analysis."
        ),
        severity=str(incident.get("severity")) if incident.get("severity") else None,
        affected_services=[service] if incident.get("service") else [],
        findings=[],
        hypotheses=hypotheses,
        root_cause=None,
        contributing_factors=[],
        recommended_action=RecommendedAction(
            action_type=RecommendedActionType.INVESTIGATE_FURTHER,
            target_service=service,
            description="A human should review this incident manually.",
            rationale="The automated analysis could not be validated.",
            evidence_ids=anchor,
        ),
        evidence=evidence,
        overall_confidence=Confidence.LOW,
        uncertainty=(
            "The automated analysis failed internal validation and was discarded: "
            + "; ".join(errors[:5])
        ),
        unavailable_evidence_sources=unavailable_sources(deps),
        investigation_metadata=build_metadata(
            deps,
            started_at=started_at,
            evidence_count=len(evidence),
            termination_reason="rca_validation_failed",
        ),
    )
