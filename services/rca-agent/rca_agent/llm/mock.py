"""Deterministic, network-free mock reasoner (ADR-021).

It exercises the *real* investigation graph — planning, tool selection,
hypothesis generation, verification, synthesis — using simple, explainable rules
over the collected evidence. Same evidence in => same RCA out, so the whole
pipeline is testable in CI without an API key.

It deliberately does not bypass anything: the graph still validates its plans,
bounds its tool calls, assigns evidence ids, and runs ``validate_report`` on its
output.
"""

from __future__ import annotations

from datetime import datetime

from rca_agent.domain import (
    Confidence,
    EvidenceSourceType,
    FindingType,
    HypothesisVerdict,
    RecommendedActionType,
)
from rca_agent.llm.base import (
    AnalysisResult,
    AnalyzeRequest,
    HypothesisVerdictItem,
    PlannedCall,
    PlanRequest,
    PlanResult,
    ProposedAction,
    ProposedFinding,
    ProposedHypothesis,
    ProposedRootCause,
    ProposedTimelineEntry,
    SynthesisResult,
    SynthesizeRequest,
    VerificationResult,
    VerifyRequest,
)
from rca_agent.schemas import Evidence

_STRONG_ANOMALY = "an anomaly window where the model score exceeded its threshold"


def _by_type(evidence: list[Evidence], *types: EvidenceSourceType) -> list[Evidence]:
    wanted = {t.value for t in types}
    return [e for e in evidence if e.source_type in wanted]


def _service_of(incident: dict[str, object]) -> str:
    return str(incident.get("service") or "unknown-service")


def _abnormal_signals(anomalies: list[Evidence]) -> list[str]:
    seen: list[str] = []
    for ev in anomalies:
        for sig in ev.content.get("abnormal_signals", []) or []:
            if isinstance(sig, str) and sig not in seen:
                seen.append(sig)
    return seen


def _strong_anomalies(anomalies: list[Evidence]) -> list[Evidence]:
    out: list[Evidence] = []
    for ev in anomalies:
        score = ev.content.get("anomaly_score")
        thresh = ev.content.get("threshold")
        if isinstance(score, int | float) and isinstance(thresh, int | float) and score > thresh:
            out.append(ev)
    return out


class MockLlmClient:
    provider = "mock"
    model: str | None = None

    async def plan(self, request: PlanRequest) -> PlanResult:
        incident_id = str(request.incident.get("id") or "")
        service = _service_of(request.incident)
        already = {e.tool_name for e in request.evidence}

        wanted: list[PlannedCall] = [
            PlannedCall(
                tool="get_anomaly_evidence",
                arguments={"incident_id": incident_id, "limit": 20},
                reason="the anomaly evidence is what formed the incident",
            ),
            PlannedCall(
                tool="get_incident_timeline",
                arguments={"incident_id": incident_id},
                reason="establish when the incident opened and moved",
            ),
            PlannedCall(
                tool="get_related_incidents",
                arguments={"service": service, "lookback_hours": 168, "limit": 10},
                reason="check whether this service has been unstable recently",
            ),
            PlannedCall(
                tool="get_service_metrics",
                arguments={
                    "service": service,
                    "metric_names": [
                        "http_server_request_duration_seconds",
                        "orders_request_failed_total",
                        "orders_created_total",
                    ],
                },
                reason="see the current operational state of the affected service",
            ),
            PlannedCall(
                tool="get_service_health",
                arguments={"service": service},
                reason="confirm whether the service is currently healthy",
            ),
        ]
        calls = [c for c in wanted if c.tool not in already]
        return PlanResult(
            calls=calls,
            rationale=(
                f"Investigate the {service} incident: read its anomaly evidence and "
                "timeline, check for related incidents, then look at the service's "
                "current metrics and health."
            ),
        )

    async def analyze(self, request: AnalyzeRequest) -> AnalysisResult:
        ev = request.evidence
        anomalies = _by_type(ev, EvidenceSourceType.ANOMALY)
        incident_ev = _by_type(ev, EvidenceSourceType.INCIDENT)
        related = _by_type(ev, EvidenceSourceType.RELATED_INCIDENT)
        health = _by_type(ev, EvidenceSourceType.SERVICE_HEALTH)
        signals = _abnormal_signals(anomalies)
        service = _service_of(request.incident)

        findings: list[ProposedFinding] = []
        for item in incident_ev:
            findings.append(
                ProposedFinding(
                    statement=(
                        f"Incident on {service} at severity "
                        f"{item.content.get('severity', 'unknown')} with "
                        f"{item.content.get('anomaly_count', 0)} anomaly window(s)."
                    ),
                    type=FindingType.OBSERVATION,
                    evidence_ids=[item.id],
                    confidence=Confidence.HIGH,
                )
            )
        for a in anomalies:
            abn = ", ".join(a.content.get("abnormal_signals", []) or []) or "no named signal"
            findings.append(
                ProposedFinding(
                    statement=(
                        f"Detector {a.content.get('detector', '?')} scored "
                        f"{a.content.get('anomaly_score')} (threshold "
                        f"{a.content.get('threshold')}); abnormal: {abn}."
                    ),
                    type=FindingType.OBSERVATION,
                    evidence_ids=[a.id],
                    confidence=Confidence.MEDIUM,
                )
            )
        if len(anomalies) >= 2 and signals:
            findings.append(
                ProposedFinding(
                    statement=(
                        f"{signals[0]} was abnormal across {len(anomalies)} consecutive windows."
                    ),
                    type=FindingType.CORRELATION,
                    evidence_ids=[a.id for a in anomalies],
                    confidence=Confidence.MEDIUM,
                )
            )
        if related:
            findings.append(
                ProposedFinding(
                    statement=f"{service} has had {len(related)} other incident(s) recently.",
                    type=FindingType.CONTRIBUTING_FACTOR,
                    evidence_ids=[r.id for r in related],
                    confidence=Confidence.LOW,
                )
            )
        for h in health:
            if h.content.get("health") not in ("ok", None) or h.content.get("readiness") not in (
                "ok",
                None,
            ):
                findings.append(
                    ProposedFinding(
                        statement=(
                            f"{service} currently reports health="
                            f"{h.content.get('health')} readiness={h.content.get('readiness')}."
                        ),
                        type=FindingType.OBSERVATION,
                        evidence_ids=[h.id],
                        confidence=Confidence.MEDIUM,
                    )
                )

        hypotheses: list[ProposedHypothesis] = []
        if signals and anomalies:
            hypotheses.append(
                ProposedHypothesis(
                    statement=(
                        f"Abnormal {signals[0]} in {service} is the primary driver of "
                        "this incident."
                    ),
                    supporting_evidence_ids=[a.id for a in anomalies],
                    contradicting_evidence_ids=[],
                    assessment="Every collected anomaly window flags this signal.",
                )
            )
        if related:
            hypotheses.append(
                ProposedHypothesis(
                    statement=(
                        f"This is a recurrence of ongoing instability in {service} rather "
                        "than a brand-new fault."
                    ),
                    supporting_evidence_ids=[r.id for r in related],
                    contradicting_evidence_ids=[a.id for a in anomalies[:1]],
                    assessment="Prior incidents exist, but the current anomaly signature "
                    "is specific.",
                )
            )
        if len(signals) >= 2:
            hypotheses.append(
                ProposedHypothesis(
                    statement=(
                        f"A shared root cause is degrading multiple signals "
                        f"({', '.join(signals[:2])}) together."
                    ),
                    supporting_evidence_ids=[a.id for a in anomalies],
                    contradicting_evidence_ids=[],
                    assessment="Multiple signals move together, suggesting one cause.",
                )
            )

        return AnalysisResult(
            findings=findings,
            hypotheses=hypotheses,
            notes=f"Analyzed {len(ev)} evidence item(s); {len(anomalies)} anomaly window(s).",
        )

    async def verify(self, request: VerifyRequest) -> VerificationResult:
        verdicts: list[HypothesisVerdictItem] = []
        for i, h in enumerate(request.hypotheses):
            s, c = len(h.supporting_evidence_ids), len(h.contradicting_evidence_ids)
            if c > s:
                verdict = HypothesisVerdict.REFUTED
            elif s >= 1 and c >= 1:
                verdict = HypothesisVerdict.CONFLICTING
            elif s >= 2 and c == 0:
                verdict = HypothesisVerdict.SUPPORTED
            else:
                verdict = HypothesisVerdict.UNVERIFIED
            verdicts.append(
                HypothesisVerdictItem(
                    index=i,
                    verdict=verdict,
                    assessment=f"{s} supporting, {c} contradicting evidence item(s).",
                )
            )

        top = verdicts[0].verdict if verdicts else HypothesisVerdict.UNVERIFIED
        have_metrics = any(e.source_type == EvidenceSourceType.METRIC for e in request.evidence)
        needs_more = (
            request.reanalysis_allowed and top is HypothesisVerdict.UNVERIFIED and not have_metrics
        )
        additional: list[PlannedCall] = []
        if needs_more:
            additional.append(
                PlannedCall(
                    tool="get_service_metrics",
                    arguments={
                        "service": _service_of(request.incident),
                        "metric_names": ["orders_request_failed_total", "orders_created_total"],
                    },
                    reason="the leading hypothesis is unverified and no metrics were read",
                )
            )
        return VerificationResult(
            verdicts=verdicts,
            needs_more_evidence=needs_more,
            additional_calls=additional,
            ready_to_conclude=not needs_more,
            notes=f"top hypothesis verdict: {top}",
        )

    async def synthesize(self, request: SynthesizeRequest) -> SynthesisResult:
        ev = request.evidence
        anomalies = _by_type(ev, EvidenceSourceType.ANOMALY)
        incident_ev = _by_type(ev, EvidenceSourceType.INCIDENT)
        related = _by_type(ev, EvidenceSourceType.RELATED_INCIDENT)
        strong = _strong_anomalies(anomalies)
        signals = _abnormal_signals(anomalies)
        service = _service_of(request.incident)
        severity = str(request.incident.get("severity") or "UNKNOWN")
        anchor_ids = [e.id for e in (incident_ev or ev[:1])]

        supported = [
            h
            for h in request.hypotheses
            if h.verdict is HypothesisVerdict.SUPPORTED and h.supporting_evidence_ids
        ]
        conflicting = [h for h in request.hypotheses if h.verdict is HypothesisVerdict.CONFLICTING]

        timeline: list[ProposedTimelineEntry] = []
        for a in anomalies[:5]:
            at = _ts(a.content.get("window_start"))
            if at is None:
                continue
            abn = ", ".join(a.content.get("abnormal_signals", []) or []) or "flagged"
            timeline.append(
                ProposedTimelineEntry(
                    at=at, description=f"anomaly window: {abn}", evidence_ids=[a.id]
                )
            )

        contributing = (
            [
                ProposedFinding(
                    statement=f"{service} had {len(related)} prior incident(s) recently.",
                    type=FindingType.CONTRIBUTING_FACTOR,
                    evidence_ids=[r.id for r in related],
                    confidence=Confidence.LOW,
                )
            ]
            if related
            else []
        )

        # Conservative on a validation-repair pass.
        if request.repair_errors or len(anomalies) == 0 or len(ev) < 2:
            return SynthesisResult(
                conclusion="insufficient_evidence",
                summary=(
                    f"Investigation of the {service} incident ({severity}). "
                    "The available evidence is not sufficient to establish a root cause."
                ),
                root_cause=None,
                contributing_factors=contributing,
                recommended_action=ProposedAction(
                    action_type=RecommendedActionType.INVESTIGATE_FURTHER,
                    target_service=service,
                    description="Gather service-side metrics and logs before concluding.",
                    rationale="Not enough independent evidence to isolate a cause.",
                    evidence_ids=anchor_ids,
                ),
                overall_confidence=Confidence.LOW,
                uncertainty=_uncertainty(request, "insufficient independent evidence"),
                timeline=timeline,
            )

        if supported and len(strong) >= 2:
            h = supported[0]
            return SynthesisResult(
                conclusion="completed",
                summary=(
                    f"{severity} incident on {service}: {h.statement} "
                    f"Supported by {len(h.supporting_evidence_ids)} evidence item(s)."
                ),
                root_cause=ProposedRootCause(
                    statement=h.statement,
                    confidence=Confidence.MEDIUM,
                    evidence_ids=list(h.supporting_evidence_ids),
                    reasoning_summary=(
                        f"{len(strong)} anomaly windows exceeded the model threshold for "
                        f"{signals[0] if signals else 'the flagged signal'}, with no "
                        "contradicting evidence."
                    ),
                ),
                contributing_factors=contributing,
                recommended_action=ProposedAction(
                    action_type=RecommendedActionType.CONTACT_SERVICE_OWNER,
                    target_service=service,
                    description=(
                        f"Have the {service} owner investigate the sustained "
                        f"{signals[0] if signals else 'signal'} degradation."
                    ),
                    rationale="Root cause is service-side and needs an owner with context.",
                    evidence_ids=list(h.supporting_evidence_ids)[:3],
                ),
                overall_confidence=Confidence.MEDIUM,
                uncertainty=_uncertainty(
                    request, "database/dependency-side metrics were unavailable"
                ),
                timeline=timeline,
            )

        # Evidence exists but no single supported cause.
        open_reason = (
            "Competing explanations conflict."
            if conflicting
            else "No hypothesis is sufficiently supported."
        )
        return SynthesisResult(
            conclusion="completed",
            summary=(
                f"{severity} incident on {service}: multiple hypotheses remain open. {open_reason}"
            ),
            root_cause=None,
            contributing_factors=contributing,
            recommended_action=ProposedAction(
                action_type=RecommendedActionType.INVESTIGATE_FURTHER,
                target_service=service,
                description="Collect dependency-side evidence to separate the open hypotheses.",
                rationale="The evidence supports several explanations equally.",
                evidence_ids=anchor_ids,
            ),
            overall_confidence=Confidence.LOW,
            uncertainty=_uncertainty(
                request,
                "no hypothesis reached SUPPORTED; "
                + ("hypotheses conflict" if conflicting else "support is weak"),
            ),
            timeline=timeline,
        )


def _ts(value: object) -> datetime | None:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _uncertainty(request: SynthesizeRequest, primary: str) -> str:
    missing = "; ".join(request.repair_errors) if request.repair_errors else ""
    base = f"Primary uncertainty: {primary}."
    if missing:
        base += f" Automated validation rejected the prior analysis: {missing}."
    base += (
        " Logs, traces, deployment history, and dependency data were not available "
        "in this deployment."
    )
    return base
