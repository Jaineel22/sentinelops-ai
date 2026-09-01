"""Runs API-triggered investigations without holding the HTTP request open.

``POST /investigations`` returns as soon as the investigation row is reserved
(one fast ``INSERT``); the bounded LangGraph run happens in a tracked background
task. No database transaction is held across that run (the property
``InvestigationService`` already guarantees). On shutdown, in-flight runs are
given a grace period to finish, then cancelled.

``run_in_background=False`` makes ``submit`` await the whole investigation inline
— used by the synchronous API tests and available for deployments that prefer a
blocking, bounded POST.
"""

from __future__ import annotations

import asyncio
import logging
import time

from rca_agent.domain import InvestigationTrigger
from rca_agent.engine import InvestigationOutcome, InvestigationService
from rca_agent.metrics import RcaMetrics
from rca_agent.schemas import Investigation

logger = logging.getLogger("rca_agent.api.runner")


class BackgroundInvestigationRunner:
    def __init__(
        self,
        service: InvestigationService,
        *,
        metrics: RcaMetrics,
        run_in_background: bool = True,
    ) -> None:
        self._service = service
        self._metrics = metrics
        self._background = run_in_background
        self._tasks: set[asyncio.Task[InvestigationOutcome | None]] = set()

    async def submit(
        self, incident_id: str, *, trigger: InvestigationTrigger
    ) -> tuple[Investigation, bool]:
        """Reserve the investigation and start it. Returns ``(investigation,
        created)`` — ``created`` is ``False`` when an investigation for this
        incident already exists (running *or* finished): the API is idempotent
        per incident, so it returns the existing one rather than starting a
        parallel or duplicate run."""

        existing = await self._service.get_existing_investigation(incident_id)
        if existing is not None:
            return existing, False

        begun = await self._service.begin(incident_id, trigger=trigger)
        if isinstance(begun, InvestigationOutcome):  # lost a race between check and begin
            return begun.investigation, False

        self._metrics.record_started(str(trigger))
        if self._background:
            task = asyncio.create_task(self._run(begun))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return begun, True

        outcome = await self._run(begun)
        return (outcome.investigation if outcome is not None else begun), True

    async def _run(self, investigation: Investigation) -> InvestigationOutcome | None:
        started = time.perf_counter()
        try:
            outcome = await self._service.run_to_completion(investigation)
        except Exception:  # run_to_completion already absorbs engine errors; be safe
            logger.exception(
                "background investigation crashed",
                extra={"investigation_id": investigation.id},
            )
            return None
        self._metrics.record_completed(
            outcome.investigation.status, duration_seconds=time.perf_counter() - started
        )
        return outcome

    async def drain(self, *, timeout: float) -> None:
        """Wait for in-flight investigations, then cancel any stragglers."""

        if not self._tasks:
            return
        pending = list(self._tasks)
        logger.info("draining %d in-flight investigation(s)", len(pending))
        _, still_running = await asyncio.wait(pending, timeout=timeout)
        for task in still_running:
            task.cancel()
        if still_running:
            await asyncio.gather(*still_running, return_exceptions=True)

    @property
    def in_flight(self) -> int:
        return len(self._tasks)
