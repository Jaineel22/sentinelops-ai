"""Incident REST API.

Read endpoints for querying incidents + their evidence and lifecycle history
(the Phase 4 boundary), plus a small set of write endpoints for manual lifecycle
transitions. No auth in Phase 3 — the service is internal (section 28).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from incident_correlator.domain import (
    EvidenceRecord,
    Incident,
    IncidentStatus,
    Severity,
    StateTransition,
)
from incident_correlator.repository import IncidentFilter, IncidentRepository
from incident_correlator.state_machine import InvalidTransitionError, validate_transition

logger = logging.getLogger("incident_correlator.api")

system_router = APIRouter(tags=["system"])
incidents_router = APIRouter(prefix="/incidents", tags=["incidents"])


# --- response models -------------------------------------------------
class HealthResponse(BaseModel):
    status: str


class ReadyResponse(BaseModel):
    status: str
    database: str
    consumer: str


class EvidenceOut(BaseModel):
    event_id: str
    detector: str
    detector_version: str
    anomaly_score: float
    threshold: float
    window_start: datetime
    window_end: datetime
    signals: dict[str, float]
    abnormal_signals: list[str]
    trace_id: str | None
    occurred_at: datetime
    correlation_reason: str

    @classmethod
    def of(cls, e: EvidenceRecord) -> EvidenceOut:
        return cls(**vars(e))


class TransitionOut(BaseModel):
    from_status: IncidentStatus | None
    to_status: IncidentStatus
    actor: str
    reason: str
    severity_at_transition: Severity | None
    created_at: datetime

    @classmethod
    def of(cls, t: StateTransition) -> TransitionOut:
        return cls(**vars(t))


class IncidentSummary(BaseModel):
    id: str
    correlation_key: str
    service: str
    environment: str
    status: IncidentStatus
    severity: Severity
    title: str
    anomaly_count: int
    distinct_abnormal_signals: int
    started_at: datetime
    last_evidence_at: datetime
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None

    @classmethod
    def of(cls, i: Incident) -> IncidentSummary:
        return cls(
            id=i.id,
            correlation_key=i.correlation_key,
            service=i.service,
            environment=i.environment,
            status=i.status,
            severity=i.severity,
            title=i.title,
            anomaly_count=i.anomaly_count,
            distinct_abnormal_signals=i.distinct_abnormal_signals,
            started_at=i.started_at,
            last_evidence_at=i.last_evidence_at,
            created_at=i.created_at,
            updated_at=i.updated_at,
            resolved_at=i.resolved_at,
        )


class IncidentDetail(IncidentSummary):
    severity_reasons: list[str]
    abnormal_signal_names: list[str]
    max_anomaly_score: float
    max_error_rate: float
    max_latency_p95_ms: float
    detector: str
    duration_seconds: float
    acknowledged_at: datetime | None
    resolution: str | None
    evidence: list[EvidenceOut]
    history: list[TransitionOut]

    @classmethod
    def of(cls, i: Incident) -> IncidentDetail:
        summary = IncidentSummary.of(i).model_dump()
        return cls(
            **summary,
            severity_reasons=i.severity_reasons,
            abnormal_signal_names=i.abnormal_signal_names,
            max_anomaly_score=i.max_anomaly_score,
            max_error_rate=i.max_error_rate,
            max_latency_p95_ms=i.max_latency_p95_ms,
            detector=i.detector,
            duration_seconds=round(i.duration_seconds, 3),
            acknowledged_at=i.acknowledged_at,
            resolution=i.resolution,
            evidence=[EvidenceOut.of(e) for e in i.evidence],
            history=[TransitionOut.of(t) for t in i.history],
        )


class TransitionRequest(BaseModel):
    to: IncidentStatus
    reason: str = Field(min_length=1, max_length=500)
    actor: str = Field(default="api", max_length=64)


class ResolveRequest(BaseModel):
    reason: str = Field(default="resolved via API", min_length=1, max_length=500)
    actor: str = Field(default="api", max_length=64)


# --- helpers -------------------------------------------------------
def _repo(request: Request) -> IncidentRepository:
    return request.app.state.repository  # type: ignore[no-any-return]


async def _load(request: Request, incident_id: str) -> Incident:
    incident = await _repo(request).get_incident(incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"incident {incident_id!r} not found")
    return incident


async def _transition(
    request: Request,
    incident_id: str,
    target: IncidentStatus,
    *,
    actor: str,
    reason: str,
) -> Incident:
    incident = await _load(request, incident_id)
    try:
        validate_transition(incident.status, target, actor=actor)
    except InvalidTransitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    updated = await _repo(request).apply_transition(incident_id, target, actor=actor, reason=reason)
    assert updated is not None
    logger.info(
        "incident transition",
        extra={
            "incident_id": incident_id,
            "from": str(incident.status),
            "to": str(target),
            "actor": actor,
        },
    )
    return updated


# --- system -------------------------------------------------------
@system_router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@system_router.get("/ready", response_model=ReadyResponse)
async def ready(request: Request) -> ReadyResponse:
    db_ok = await _repo(request).health_check()
    consumer = request.app.state.consumer
    consumer_ok = consumer is None or consumer.healthy
    if not (db_ok and consumer_ok):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "not-ready",
                "database": "ok" if db_ok else "down",
                "consumer": "ok" if consumer_ok else "down",
            },
        )
    return ReadyResponse(status="ready", database="ok", consumer="ok" if consumer_ok else "n/a")


@system_router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# --- incidents ---------------------------------------------------
@incidents_router.get("", response_model=list[IncidentSummary])
async def list_incidents(
    request: Request,
    status_: Annotated[IncidentStatus | None, Query(alias="status")] = None,
    service: str | None = None,
    severity: Severity | None = None,
    since: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[IncidentSummary]:
    rows = await _repo(request).list_incidents(
        IncidentFilter(
            status=status_,
            service=service,
            severity=severity,
            since=since,
            limit=limit,
            offset=offset,
        )
    )
    return [IncidentSummary.of(i) for i in rows]


@incidents_router.get("/{incident_id}", response_model=IncidentDetail)
async def get_incident(incident_id: str, request: Request) -> IncidentDetail:
    return IncidentDetail.of(await _load(request, incident_id))


@incidents_router.get("/{incident_id}/evidence", response_model=list[EvidenceOut])
async def get_evidence(incident_id: str, request: Request) -> list[EvidenceOut]:
    evidence = await _repo(request).get_evidence(incident_id)
    if evidence is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"incident {incident_id!r} not found")
    return [EvidenceOut.of(e) for e in evidence]


@incidents_router.get("/{incident_id}/history", response_model=list[TransitionOut])
async def get_history(incident_id: str, request: Request) -> list[TransitionOut]:
    history = await _repo(request).get_history(incident_id)
    if history is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"incident {incident_id!r} not found")
    return [TransitionOut.of(t) for t in history]


@incidents_router.post("/{incident_id}/acknowledge", response_model=IncidentDetail)
async def acknowledge(incident_id: str, request: Request) -> IncidentDetail:
    updated = await _transition(
        request,
        incident_id,
        IncidentStatus.ACKNOWLEDGED,
        actor="api",
        reason="acknowledged via API",
    )
    return IncidentDetail.of(updated)


@incidents_router.post("/{incident_id}/resolve", response_model=IncidentDetail)
async def resolve(
    incident_id: str, request: Request, body: ResolveRequest | None = None
) -> IncidentDetail:
    body = body or ResolveRequest()
    updated = await _transition(
        request, incident_id, IncidentStatus.RESOLVED, actor=body.actor, reason=body.reason
    )
    return IncidentDetail.of(updated)


@incidents_router.post("/{incident_id}/transition", response_model=IncidentDetail)
async def transition(incident_id: str, body: TransitionRequest, request: Request) -> IncidentDetail:
    updated = await _transition(request, incident_id, body.to, actor=body.actor, reason=body.reason)
    return IncidentDetail.of(updated)
