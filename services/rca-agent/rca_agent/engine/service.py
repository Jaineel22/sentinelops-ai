"""The investigation orchestrator.

    begin_investigation (idempotent)  ->  run the LangGraph  ->  persist the result

The graph runs *between* the two database transactions — a transaction is never
held open across model calls. Any exception the graph does not itself convert to
a terminal state is caught here and becomes ``FAILED`` (never propagates).

``investigate()`` runs all three steps in one call (Kafka consumer, scenario
script). ``begin()`` + ``run_to_completion()`` are the same two halves exposed
separately so the HTTP API can return the created investigation immediately
(``202``) and run the graph as a background task (Sub-phase 4E). The transaction
boundaries are identical either way.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from rca_agent.config import Settings
from rca_agent.domain import Confidence, InvestigationStatus, InvestigationTrigger
from rca_agent.engine.deps import GraphDeps
from rca_agent.engine.graph import build_graph, recursion_limit
from rca_agent.limits import ResourceUsage
from rca_agent.llm.base import LlmClient
from rca_agent.repository import DuplicateActiveInvestigationError, InvestigationRepository
from rca_agent.schemas import Evidence, Investigation, InvestigationStep, RCAReport
from rca_agent.state import InvestigationState
from rca_agent.tools import ToolContext, ToolRegistry

logger = logging.getLogger("rca_agent.engine")

try:  # pragma: no cover - import shape varies across langgraph patch releases
    from langgraph.errors import GraphRecursionError
except ImportError:  # pragma: no cover

    class GraphRecursionError(Exception):  # type: ignore[no-redef]
        pass


@dataclass(frozen=True)
class InvestigationOutcome:
    investigation: Investigation
    report: RCAReport | None
    steps: list[InvestigationStep]
    evidence: list[Evidence]

    @property
    def already_running(self) -> bool:
        return self.report is None and self.investigation.status in {
            InvestigationStatus.PENDING,
            InvestigationStatus.PLANNING,
            InvestigationStatus.COLLECTING_EVIDENCE,
            InvestigationStatus.ANALYZING,
            InvestigationStatus.VERIFYING,
        }


class InvestigationService:
    def __init__(
        self,
        *,
        repository: InvestigationRepository,
        registry: ToolRegistry,
        llm_client: LlmClient,
        settings: Settings,
    ) -> None:
        self._repo = repository
        self._registry = registry
        self._llm = llm_client
        self._settings = settings

    async def investigate(
        self, incident_id: str, *, trigger: InvestigationTrigger
    ) -> InvestigationOutcome:
        """Begin, run, and persist an investigation in one call."""

        begun = await self.begin(incident_id, trigger=trigger)
        if isinstance(begun, InvestigationOutcome):
            return begun
        return await self.run_to_completion(begun)

    async def get_existing_investigation(self, incident_id: str) -> Investigation | None:
        """The most recent investigation for this incident (any status), or
        ``None``. The Kafka consumer uses this to skip a redelivered
        ``incident.opened`` — that event is emitted once per incident, so any
        existing investigation means it has already been handled."""

        return await self._repo.get_latest_investigation(incident_id)

    async def begin(
        self, incident_id: str, *, trigger: InvestigationTrigger
    ) -> Investigation | InvestigationOutcome:
        """Reserve the single active investigation for this incident (one INSERT).

        Returns the fresh :class:`Investigation` on success, or an
        :class:`InvestigationOutcome` wrapping the already-active investigation if
        one exists (``already_running`` is ``True``) — the idempotency guard for a
        redelivered trigger.
        """

        mode = self._settings.rca.mode
        try:
            return await self._repo.begin_investigation(incident_id, trigger=trigger, mode=mode)
        except DuplicateActiveInvestigationError:
            existing = await self._repo.get_active_investigation(incident_id)
            if existing is None:  # pragma: no cover - lost a race; treat as transient
                raise
            logger.info(
                "investigation already active for incident",
                extra={"incident_id": incident_id, "investigation_id": existing.id},
            )
            return InvestigationOutcome(existing, None, [], [])

    async def run_to_completion(self, investigation: Investigation) -> InvestigationOutcome:
        """Run the bounded LangGraph for an already-begun investigation and
        persist the terminal result. Never raises: any uncaught engine error
        becomes a persisted ``FAILED`` outcome."""

        incident_id = investigation.incident_id
        mode = self._settings.rca.mode
        limits = self._settings.rca.resource_limits()
        usage = ResourceUsage(started_at=datetime.now(tz=UTC))
        deps = GraphDeps(
            incident_id=incident_id,
            investigation_id=investigation.id,
            mode=mode,
            registry=self._registry,
            llm=self._llm,
            limits=limits,
            tool_context=ToolContext(max_evidence_items=limits.max_evidence_items),
            usage=usage,
        )

        initial: InvestigationState = {
            "incident_id": incident_id,
            "investigation_id": investigation.id,
            "mode": mode,
            "status": InvestigationStatus.PENDING,
            "evidence": [],
            "steps": [],
        }

        status = InvestigationStatus.FAILED
        reason = "engine did not complete"
        report: RCAReport | None = None
        steps: list[InvestigationStep] = []
        evidence: list[Evidence] = []

        try:
            graph = build_graph(deps)
            final = await graph.ainvoke(initial, config={"recursion_limit": recursion_limit(deps)})
            status = final.get("status", InvestigationStatus.FAILED)
            reason = final.get("terminal_reason") or "unknown"
            report = final.get("rca")
            steps = list(final.get("steps", []))
            evidence = list(final.get("evidence", []))
        except GraphRecursionError:
            logger.warning("investigation hit the graph recursion limit")
            status, reason = InvestigationStatus.TIMED_OUT, "graph recursion limit reached"
        except Exception as exc:
            logger.exception("investigation engine failed", extra={"incident_id": incident_id})
            status, reason = InvestigationStatus.FAILED, f"engine error: {type(exc).__name__}"

        if not status.is_terminal:  # defensive: never leave an investigation "active"
            status, reason = InvestigationStatus.FAILED, f"non-terminal status {status}"

        completed = await self._repo.complete_investigation(
            investigation.id,
            status=status,
            termination_reason=reason[:200],
            overall_confidence=str(report.overall_confidence if report else Confidence.UNKNOWN),
            model=self._llm.model,
            steps=steps,
            evidence=evidence,
            report=report,
        )
        logger.info(
            "investigation complete",
            extra={
                "incident_id": incident_id,
                "investigation_id": completed.id,
                "status": str(status),
                "tool_calls": completed.tool_call_count,
                "evidence": completed.evidence_count,
                "has_root_cause": report is not None and report.root_cause is not None,
            },
        )
        return InvestigationOutcome(completed, report, steps, evidence)
