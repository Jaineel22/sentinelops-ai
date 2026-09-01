"""The investigation graph nodes (ADR-021).

Each node does one job, records concise operational steps, and returns a partial
:class:`InvestigationState`. Deterministic code is authoritative throughout:
the model proposes plans / findings / hypotheses / a synthesis, and this module
validates, id-stamps, bounds, and — for the final report — runs
``validate_report`` before anything is accepted.
"""

from __future__ import annotations

from typing import Any

from rca_agent.domain import HypothesisVerdict, InvestigationStatus, StepKind
from rca_agent.engine.deps import GraphDeps
from rca_agent.engine.plan import ValidatedCall, validate_plan
from rca_agent.engine.steps import step
from rca_agent.engine.synthesis import build_fallback_report, build_rca_report
from rca_agent.engine.tooling import run_validated_call
from rca_agent.limits import LimitExceeded, check_limits
from rca_agent.llm.base import (
    AnalyzeRequest,
    LlmError,
    LlmTimeout,
    PlanRequest,
    SynthesizeRequest,
    VerifyRequest,
)
from rca_agent.schemas import Evidence, Hypothesis
from rca_agent.state import InvestigationState
from rca_agent.tools.contracts import GetIncidentRequest
from rca_agent.tools.names import ToolName
from rca_agent.validation import RcaValidationError, validate_report

_S = InvestigationStatus


def _terminal(status: _S, reason: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"status": status, "terminal_reason": reason, "next": "END", **(extra or {})}


def _llm_terminal(exc: LlmError) -> dict[str, Any]:
    if isinstance(exc, LlmTimeout):
        return _terminal(_S.TIMED_OUT, f"llm timeout: {exc}")
    return _terminal(_S.FAILED, f"llm error: {type(exc).__name__}: {exc}")


# When a count limit (not the wall clock) is hit at the top of a node, the
# investigation degrades gracefully to the next stage rather than looping or
# failing — ``validate`` still always runs (the safety gate, ADR-021).
_DEGRADE_NEXT: dict[str, tuple[str, InvestigationStatus]] = {
    "plan": ("synthesize", _S.VERIFYING),
    "collect": ("analyze", _S.ANALYZING),
    "analyze": ("synthesize", _S.VERIFYING),
    "verify": ("synthesize", _S.VERIFYING),
    "synthesize": ("validate", _S.VERIFYING),
}


def _guard(deps: GraphDeps, node: str, phase: InvestigationStatus) -> dict[str, Any] | None:
    """Deterministic limit check at the top of every node. ``None`` => proceed."""

    try:
        check_limits(deps.usage, deps.limits, now=deps.now())
    except LimitExceeded as exc:
        if exc.kind == "time":
            return _terminal(_S.TIMED_OUT, f"investigation timed out ({exc.kind})")
        nxt, status = _DEGRADE_NEXT.get(node, ("", _S.FAILED))
        if not nxt:
            return _terminal(_S.INSUFFICIENT_EVIDENCE, f"{exc.kind} limit reached")
        note = step(
            deps,
            kind=StepKind.LIMIT,
            phase=phase,
            description=f"{exc.kind} limit reached; advancing to {nxt}",
        )
        return {"status": status, "steps": [note], "next": nxt, "queue": []}
    return None


class InvestigationNodes:
    def __init__(self, deps: GraphDeps) -> None:
        self.deps = deps

    # --- 1. initialize ------------------------------------------------
    async def initialize(self, state: InvestigationState) -> dict[str, Any]:
        deps = self.deps
        started = step(
            deps,
            kind=StepKind.PLAN,
            phase=_S.PENDING,
            description=f"investigation started for incident {deps.incident_id} (mode={deps.mode})",
        )
        try:
            result = await run_validated_call(
                deps,
                ValidatedCall(
                    ToolName.GET_INCIDENT,
                    GetIncidentRequest(incident_id=deps.incident_id).model_dump(mode="json"),
                ),
            )
        except LimitExceeded as exc:
            return _terminal(
                _S.TIMED_OUT,
                f"limit reached before loading incident: {exc.kind}",
                {"steps": [started]},
            )

        if not result.ok or not result.evidence:
            reason = result.error.message if result.error else "no incident data returned"
            return _terminal(
                _S.FAILED,
                f"could not load incident {deps.incident_id}: {reason}",
                {"steps": [started]},
            )

        incident_ev = result.evidence[0]
        loaded = step(
            deps,
            kind=StepKind.TOOL_CALL,
            phase=_S.PLANNING,
            description=f"loaded incident {deps.incident_id} via get_incident",
            tool_name=str(ToolName.GET_INCIDENT),
            evidence_ids=[incident_ev.id],
        )
        return {
            "status": _S.PLANNING,
            "incident": dict(incident_ev.content),
            "evidence": [incident_ev],
            "steps": [started, loaded],
            "queue": [],
            "reanalysis_count": 0,
            "repair_count": 0,
            "next": "plan",
        }

    # --- 2. plan ----------------------------------------------------
    async def plan(self, state: InvestigationState) -> dict[str, Any]:
        if (g := _guard(self.deps, "plan", _S.PLANNING)) is not None:
            return g
        deps = self.deps
        incident = state.get("incident") or {}
        evidence: list[Evidence] = state.get("evidence", [])
        try:
            result = await deps.llm.plan(
                PlanRequest(
                    incident=incident,
                    evidence=evidence,
                    tool_specs=[s.model_dump(mode="json") for s in deps.registry.specs()],
                )
            )
        except LlmError as exc:
            return _llm_terminal(exc)

        remaining = max(deps.limits.max_tool_calls - deps.usage.tool_calls, 0)
        validated = validate_plan(result.calls, deps.registry, max_calls=remaining)

        if not validated.accepted:
            # one bounded repair attempt
            try:
                retry = await deps.llm.plan(
                    PlanRequest(
                        incident=incident,
                        evidence=evidence,
                        tool_specs=[s.model_dump(mode="json") for s in deps.registry.specs()],
                        repair_errors=validated.rejected or ["the plan contained no usable calls"],
                    )
                )
            except LlmError as exc:
                return _llm_terminal(exc)
            validated = validate_plan(retry.calls, deps.registry, max_calls=remaining)

        plan_step = step(
            deps,
            kind=StepKind.PLAN,
            phase=_S.PLANNING,
            description=(
                f"planned {len(validated.accepted)} evidence tool call(s): "
                + ", ".join(str(c.tool_name) for c in validated.accepted)
                + (f"; rejected: {'; '.join(validated.rejected)}" if validated.rejected else "")
            ),
        )

        if not validated.accepted:
            return _terminal(
                _S.INSUFFICIENT_EVIDENCE,
                "no valid evidence-collection plan could be produced",
                {"steps": [plan_step], "plan_rationale": result.rationale},
            )

        return {
            "status": _S.COLLECTING_EVIDENCE,
            "queue": list(validated.accepted),
            "plan_rationale": result.rationale,
            "steps": [plan_step],
            "next": "collect",
        }

    # --- 3. collect (bounded loop) ---------------------------------
    async def collect(self, state: InvestigationState) -> dict[str, Any]:
        if (g := _guard(self.deps, "collect", _S.COLLECTING_EVIDENCE)) is not None:
            return g
        deps = self.deps
        queue: list[ValidatedCall] = list(state.get("queue", []))
        if not queue:
            return {"status": _S.ANALYZING, "next": "analyze"}

        call = queue.pop(0)
        try:
            result = await run_validated_call(deps, call)
        except LimitExceeded as exc:
            if exc.kind == "time":
                return _terminal(_S.TIMED_OUT, "investigation timed out during evidence collection")
            note = step(
                deps,
                kind=StepKind.LIMIT,
                phase=_S.COLLECTING_EVIDENCE,
                description=f"stopped collecting evidence: {exc.kind} limit reached",
            )
            return {"status": _S.ANALYZING, "queue": [], "steps": [note], "next": "analyze"}

        desc = f"called {call.tool_name} -> {result.status.value}"
        if result.error:
            desc += f" ({result.error.message})"
        s = step(
            deps,
            kind=StepKind.TOOL_CALL,
            phase=_S.COLLECTING_EVIDENCE,
            description=desc,
            tool_name=str(call.tool_name),
            evidence_ids=[e.id for e in result.evidence],
        )
        update: dict[str, Any] = {"queue": queue, "steps": [s]}
        if result.evidence:
            update["evidence"] = list(result.evidence)
        more = bool(queue) and deps.tool_context.remaining_evidence() > 0
        update["next"] = "collect" if more else "analyze"
        if not more:
            update["status"] = _S.ANALYZING
        return update

    # --- 4. analyze ----------------------------------------------
    async def analyze(self, state: InvestigationState) -> dict[str, Any]:
        if (g := _guard(self.deps, "analyze", _S.ANALYZING)) is not None:
            return g
        deps = self.deps
        incident = state.get("incident") or {}
        drained_steps, drained_evidence = await self._drain_extra_queue(state)
        evidence: list[Evidence] = list(state.get("evidence", [])) + drained_evidence

        try:
            result = await deps.llm.analyze(AnalyzeRequest(incident=incident, evidence=evidence))
        except LlmError as exc:
            return _llm_terminal(exc)

        hyps = result.hypotheses[: deps.limits.max_hypotheses]
        s = step(
            deps,
            kind=StepKind.ANALYSIS,
            phase=_S.ANALYZING,
            description=(
                f"analyzed {len(evidence)} evidence item(s): "
                f"{len(result.findings)} finding(s), {len(hyps)} hypothesis(es)"
            ),
        )
        return {
            "status": _S.VERIFYING,
            "proposed_findings": list(result.findings),
            "proposed_hypotheses": list(hyps),
            "steps": [*drained_steps, s],
            "next": "verify",
        }

    async def _drain_extra_queue(
        self, state: InvestigationState
    ) -> tuple[list[Any], list[Evidence]]:
        deps = self.deps
        queue: list[ValidatedCall] = list(state.get("queue", []))
        steps: list[Any] = []
        evidence: list[Evidence] = []
        while queue and deps.tool_context.remaining_evidence() > 0:
            call = queue.pop(0)
            try:
                result = await run_validated_call(deps, call)
            except LimitExceeded:
                break
            steps.append(
                step(
                    deps,
                    kind=StepKind.TOOL_CALL,
                    phase=_S.ANALYZING,
                    description=f"follow-up: called {call.tool_name} -> {result.status.value}",
                    tool_name=str(call.tool_name),
                    evidence_ids=[e.id for e in result.evidence],
                )
            )
            evidence.extend(result.evidence)
        return steps, evidence

    # --- 5. verify ---------------------------------------------
    async def verify(self, state: InvestigationState) -> dict[str, Any]:
        if (g := _guard(self.deps, "verify", _S.VERIFYING)) is not None:
            return g
        deps = self.deps
        incident = state.get("incident") or {}
        evidence: list[Evidence] = state.get("evidence", [])
        proposed = state.get("proposed_hypotheses", [])
        findings = state.get("proposed_findings", [])
        known = {e.id for e in evidence}
        reanalysis_count = state.get("reanalysis_count", 0)
        allow_reanalysis = reanalysis_count < 1

        try:
            result = await deps.llm.verify(
                VerifyRequest(
                    incident=incident,
                    evidence=evidence,
                    findings=findings,
                    hypotheses=proposed,
                    reanalysis_allowed=allow_reanalysis,
                )
            )
        except LlmError as exc:
            return _llm_terminal(exc)

        verdicts = {v.index: v for v in result.verdicts}
        hypotheses: list[Hypothesis] = []
        for i, ph in enumerate(proposed):
            v = verdicts.get(i)
            hypotheses.append(
                Hypothesis(
                    id=f"hy_{i + 1:03d}",
                    statement=ph.statement,
                    supporting_evidence_ids=[x for x in ph.supporting_evidence_ids if x in known],
                    contradicting_evidence_ids=[
                        x for x in ph.contradicting_evidence_ids if x in known
                    ],
                    assessment=(v.assessment if v else "not assessed") or "not assessed",
                    verdict=v.verdict if v else _default_verdict(ph, known),
                )
            )

        want_more = (
            result.needs_more_evidence and allow_reanalysis and bool(result.additional_calls)
        )
        if want_more:
            remaining = max(deps.limits.max_tool_calls - deps.usage.tool_calls, 0)
            extra = validate_plan(
                result.additional_calls, deps.registry, max_calls=min(remaining, 3)
            )
            if extra.accepted:
                s = step(
                    deps,
                    kind=StepKind.VERIFICATION,
                    phase=_S.VERIFYING,
                    description=(
                        "verification inconclusive; collecting "
                        f"{len(extra.accepted)} more evidence item(s) then re-analyzing"
                    ),
                )
                return {
                    "status": _S.ANALYZING,
                    "hypotheses": hypotheses,
                    "queue": list(extra.accepted),
                    "reanalysis_count": reanalysis_count + 1,
                    "steps": [s],
                    "next": "analyze",
                }

        supported = sum(1 for h in hypotheses if h.verdict is HypothesisVerdict.SUPPORTED)
        conflicting = sum(1 for h in hypotheses if h.verdict is HypothesisVerdict.CONFLICTING)
        s = step(
            deps,
            kind=StepKind.VERIFICATION,
            phase=_S.VERIFYING,
            description=(
                f"verified {len(hypotheses)} hypothesis(es): "
                f"{supported} supported, {conflicting} conflicting"
            ),
        )
        return {
            "status": _S.VERIFYING,
            "hypotheses": hypotheses,
            "steps": [s],
            "next": "synthesize",
        }

    # --- 6. synthesize -------------------------------------------
    async def synthesize(self, state: InvestigationState) -> dict[str, Any]:
        if (g := _guard(self.deps, "synthesize", _S.VERIFYING)) is not None:
            return g
        deps = self.deps
        incident = state.get("incident") or {}
        evidence: list[Evidence] = state.get("evidence", [])
        hypotheses: list[Hypothesis] = state.get("hypotheses", [])
        repair_errors: list[str] = state.get("repair_errors", [])

        try:
            synthesis = await deps.llm.synthesize(
                SynthesizeRequest(
                    incident=incident,
                    investigation_id=deps.investigation_id,
                    evidence=evidence,
                    findings=state.get("proposed_findings", []),
                    hypotheses=hypotheses,
                    repair_errors=repair_errors,
                )
            )
        except LlmError as exc:
            return _llm_terminal(exc)

        report = build_rca_report(
            deps,
            incident_id=deps.incident_id,
            investigation_id=deps.investigation_id,
            incident=incident,
            evidence=evidence,
            proposed_findings=state.get("proposed_findings", []),
            hypotheses=hypotheses,
            synthesis=synthesis,
            started_at=deps.usage.started_at,
        )
        s = step(
            deps,
            kind=StepKind.RCA,
            phase=_S.VERIFYING,
            description=(
                f"synthesized RCA candidate: conclusion={synthesis.conclusion}, "
                f"root_cause={'yes' if report.root_cause else 'undetermined'}"
            ),
        )
        return {
            "status": _S.VERIFYING,
            "synthesis": synthesis,
            "rca": report,
            "steps": [s],
            "next": "validate",
        }

    # --- 7. validate (deterministic) ---------------------------
    async def validate(self, state: InvestigationState) -> dict[str, Any]:
        deps = self.deps
        report = state.get("rca")
        evidence: list[Evidence] = state.get("evidence", [])
        known = {e.id for e in evidence}
        repair_count = state.get("repair_count", 0)

        if report is None:
            # synthesize was skipped by a limit guard — build a safe report here
            report = build_fallback_report(
                deps,
                incident_id=deps.incident_id,
                investigation_id=deps.investigation_id,
                incident=state.get("incident") or {},
                evidence=evidence,
                hypotheses=state.get("hypotheses", []),
                errors=["synthesis stage was not reached"],
                started_at=deps.usage.started_at,
            )
            s = step(
                deps,
                kind=StepKind.VALIDATION,
                phase=_S.INSUFFICIENT_EVIDENCE,
                description="synthesis not reached; returning a safe insufficient-evidence report",
            )
            try:
                validate_report(report, known)
            except RcaValidationError as exc:  # pragma: no cover
                return _terminal(_S.FAILED, f"fallback RCA invalid: {exc}", {"steps": [s]})
            return _terminal(
                _S.INSUFFICIENT_EVIDENCE, "synthesis_not_reached", {"rca": report, "steps": [s]}
            )

        try:
            validate_report(report, known)
        except RcaValidationError as exc:
            if repair_count < deps.max_repair_attempts:
                s = step(
                    deps,
                    kind=StepKind.VALIDATION,
                    phase=_S.VERIFYING,
                    description=f"RCA validation failed ({len(exc.errors)} issue(s)); repairing",
                )
                return {
                    "status": _S.VERIFYING,
                    "repair_count": repair_count + 1,
                    "repair_errors": exc.errors,
                    "steps": [s],
                    "next": "synthesize",
                }
            fallback = build_fallback_report(
                deps,
                incident_id=deps.incident_id,
                investigation_id=deps.investigation_id,
                incident=state.get("incident") or {},
                evidence=evidence,
                hypotheses=state.get("hypotheses", []),
                errors=exc.errors,
                started_at=deps.usage.started_at,
            )
            try:
                validate_report(fallback, known)
            except RcaValidationError as exc2:  # pragma: no cover - fallback is minimal
                return _terminal(_S.FAILED, f"even the fallback RCA failed validation: {exc2}")
            s = step(
                deps,
                kind=StepKind.VALIDATION,
                phase=_S.INSUFFICIENT_EVIDENCE,
                description=(
                    "RCA validation failed after repair; "
                    "returning a safe insufficient-evidence report"
                ),
            )
            return _terminal(
                _S.INSUFFICIENT_EVIDENCE,
                "rca_validation_failed",
                {"rca": fallback, "steps": [s]},
            )

        s = step(
            deps,
            kind=StepKind.VALIDATION,
            phase=report.status,
            description=f"RCA validation passed; investigation {report.status.value.lower()}",
        )
        return _terminal(report.status, "analysis_complete", {"steps": [s]})


def _default_verdict(ph: Any, known: set[str]) -> HypothesisVerdict:
    s = len([x for x in ph.supporting_evidence_ids if x in known])
    c = len([x for x in ph.contradicting_evidence_ids if x in known])
    if c > s:
        return HypothesisVerdict.REFUTED
    if s >= 1 and c >= 1:
        return HypothesisVerdict.CONFLICTING
    if s >= 2:
        return HypothesisVerdict.SUPPORTED
    return HypothesisVerdict.UNVERIFIED
