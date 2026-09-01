"""FastAPI routers for the rca-agent (Sub-phase 4E)."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from rca_agent.api.runner import BackgroundInvestigationRunner
from rca_agent.api.schemas import CreateInvestigationRequest, InvestigationDetail
from rca_agent.domain import InvestigationTrigger
from rca_agent.repository import InvestigationRepository
from rca_agent.schemas import Investigation, InvestigationStep

logger = logging.getLogger("rca_agent.api")

system_router = APIRouter(tags=["system"])
investigations_router = APIRouter(tags=["investigations"])


def _repo(request: Request) -> InvestigationRepository:
    return request.app.state.repository  # type: ignore[no-any-return]


def _runner(request: Request) -> BackgroundInvestigationRunner:
    return request.app.state.runner  # type: ignore[no-any-return]


async def _detail(request: Request, investigation: Investigation) -> InvestigationDetail:
    repo = _repo(request)
    steps = await repo.get_steps(investigation.id) or []
    report = await repo.get_report(investigation.id)
    return InvestigationDetail(investigation=investigation, steps=steps, report=report)


# --- system -----------------------------------------------------------
@system_router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@system_router.get("/ready")
async def ready(request: Request) -> Response:
    database = getattr(request.app.state, "database", None)
    consumer = getattr(request.app.state, "consumer", None)
    db_ok = database is None or await database.ping()
    consumer_ok = consumer is None or consumer.healthy
    ok = bool(db_ok and consumer_ok)
    body = {
        "status": "ready" if ok else "not-ready",
        "database": "ok" if db_ok else "down",
        "consumer": "ok" if consumer_ok else ("n/a" if consumer is None else "down"),
    }
    return Response(
        json.dumps(body),
        status_code=status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        media_type="application/json",
    )


@system_router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# --- investigations --------------------------------------------------
@investigations_router.post(
    "/investigations", response_model=InvestigationDetail, status_code=status.HTTP_202_ACCEPTED
)
async def create_investigation(
    body: CreateInvestigationRequest, request: Request, response: Response
) -> InvestigationDetail:
    """Trigger a manual investigation. ``202`` with the new (PENDING)
    investigation; ``200`` with the existing one if this incident has already
    been investigated (idempotent per incident — no parallel or duplicate runs).
    ``422`` for a malformed incident id."""

    investigation, created = await _runner(request).submit(
        body.incident_id, trigger=InvestigationTrigger.MANUAL
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    logger.info(
        "investigation requested via API",
        extra={
            "incident_id": body.incident_id,
            "investigation_id": investigation.id,
            "newly_created": created,
        },
    )
    return await _detail(request, investigation)


@investigations_router.get("/investigations/{investigation_id}", response_model=InvestigationDetail)
async def get_investigation(investigation_id: str, request: Request) -> InvestigationDetail:
    investigation = await _repo(request).get_investigation(investigation_id)
    if investigation is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"investigation {investigation_id!r} not found"
        )
    return await _detail(request, investigation)


@investigations_router.get(
    "/investigations/{investigation_id}/steps", response_model=list[InvestigationStep]
)
async def get_investigation_steps(
    investigation_id: str, request: Request
) -> list[InvestigationStep]:
    """Just the operational trace — cheap to poll while an investigation runs.
    Concise action/result entries only, never private model reasoning. ``404``
    if the investigation is unknown."""

    steps = await _repo(request).get_steps(investigation_id)
    if steps is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"investigation {investigation_id!r} not found"
        )
    return steps


@investigations_router.get(
    "/incidents/{incident_id}/investigation", response_model=InvestigationDetail
)
async def get_incident_investigation(incident_id: str, request: Request) -> InvestigationDetail:
    investigation = await _repo(request).get_latest_investigation(incident_id)
    if investigation is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"no investigation exists for incident {incident_id!r}",
        )
    return await _detail(request, investigation)
