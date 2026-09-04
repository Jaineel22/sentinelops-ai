"""PostgreSQL-backed :class:`~incident_correlator.repository.IncidentRepository`.

The unit of work is one SQLAlchemy transaction: it commits when the ``async
with`` block exits cleanly and rolls back on any exception, so a failure
mid-processing leaves no partial incident.
"""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from incident_correlator.db.engine import Database
from incident_correlator.db.models import (
    EvidenceRow,
    IncidentRelationRow,
    IncidentRow,
    StateHistoryRow,
)
from incident_correlator.domain import (
    EvidenceRecord,
    Incident,
    IncidentRelation,
    IncidentRelationType,
    IncidentStatus,
    Severity,
    StateTransition,
)
from incident_correlator.repository import (
    DuplicateActiveIncidentError,
    IncidentFilter,
    IncidentUnitOfWork,
)

_ACTIVE_SQL = ("OPEN", "ACKNOWLEDGED", "INVESTIGATING", "MITIGATING")


def _utc(dt: datetime) -> datetime:
    """SQLite drops tzinfo on ``DateTime(timezone=True)``; Postgres keeps it.
    Normalise so the domain layer always sees UTC-aware datetimes."""

    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _utc_opt(dt: datetime | None) -> datetime | None:
    return _utc(dt) if dt is not None else None


# --- row <-> domain mapping ------------------------------------------
def _incident_to_row(inc: Incident) -> IncidentRow:
    return IncidentRow(
        id=inc.id,
        correlation_key=inc.correlation_key,
        service=inc.service,
        environment=inc.environment,
        status=str(inc.status),
        severity=str(inc.severity),
        severity_reasons=list(inc.severity_reasons),
        title=inc.title,
        anomaly_count=inc.anomaly_count,
        abnormal_signal_names=list(inc.abnormal_signal_names),
        max_anomaly_score=inc.max_anomaly_score,
        max_error_rate=inc.max_error_rate,
        max_latency_p95_ms=inc.max_latency_p95_ms,
        detector=inc.detector,
        started_at=inc.started_at,
        last_evidence_at=inc.last_evidence_at,
        created_at=inc.created_at,
        updated_at=inc.updated_at,
        acknowledged_at=inc.acknowledged_at,
        resolved_at=inc.resolved_at,
        resolution=inc.resolution,
    )


def _apply_incident(row: IncidentRow, inc: Incident) -> None:
    row.status = str(inc.status)
    row.severity = str(inc.severity)
    row.severity_reasons = list(inc.severity_reasons)
    row.title = inc.title
    row.anomaly_count = inc.anomaly_count
    row.abnormal_signal_names = list(inc.abnormal_signal_names)
    row.max_anomaly_score = inc.max_anomaly_score
    row.max_error_rate = inc.max_error_rate
    row.max_latency_p95_ms = inc.max_latency_p95_ms
    row.started_at = inc.started_at
    row.last_evidence_at = inc.last_evidence_at
    row.updated_at = inc.updated_at
    row.acknowledged_at = inc.acknowledged_at
    row.resolved_at = inc.resolved_at
    row.resolution = inc.resolution


def _row_to_incident(row: IncidentRow, *, with_children: bool = True) -> Incident:
    inc = Incident(
        id=row.id,
        correlation_key=row.correlation_key,
        service=row.service,
        environment=row.environment,
        status=IncidentStatus(row.status),
        severity=Severity(row.severity),
        severity_reasons=list(row.severity_reasons),
        title=row.title,
        anomaly_count=row.anomaly_count,
        abnormal_signal_names=list(row.abnormal_signal_names),
        max_anomaly_score=row.max_anomaly_score,
        max_error_rate=row.max_error_rate,
        max_latency_p95_ms=row.max_latency_p95_ms,
        detector=row.detector,
        started_at=_utc(row.started_at),
        last_evidence_at=_utc(row.last_evidence_at),
        created_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
        acknowledged_at=_utc_opt(row.acknowledged_at),
        resolved_at=_utc_opt(row.resolved_at),
        resolution=row.resolution,
    )
    if with_children:
        inc.evidence = [
            EvidenceRecord(
                event_id=e.event_id,
                detector=e.detector,
                detector_version=e.detector_version,
                anomaly_score=e.anomaly_score,
                threshold=e.threshold,
                window_start=_utc(e.window_start),
                window_end=_utc(e.window_end),
                signals=dict(e.signals),
                abnormal_signals=list(e.abnormal_signals),
                trace_id=e.trace_id,
                occurred_at=_utc(e.occurred_at),
                correlation_reason=e.correlation_reason,
            )
            for e in row.evidence
        ]
        inc.history = [
            StateTransition(
                from_status=IncidentStatus(h.from_status) if h.from_status else None,
                to_status=IncidentStatus(h.to_status),
                actor=h.actor,
                reason=h.reason,
                severity_at_transition=(
                    Severity(h.severity_at_transition) if h.severity_at_transition else None
                ),
                created_at=_utc(h.created_at),
            )
            for h in row.history
        ]
    return inc


class _SqlUoW:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def evidence_exists(self, event_id: str) -> bool:
        found = await self._s.scalar(select(EvidenceRow.id).where(EvidenceRow.event_id == event_id))
        return found is not None

    async def lock_active_incident(self, correlation_key: str) -> Incident | None:
        stmt = (
            select(IncidentRow)
            .where(
                IncidentRow.correlation_key == correlation_key,
                IncidentRow.status.in_(_ACTIVE_SQL),
            )
            .order_by(IncidentRow.last_evidence_at.desc())
            .limit(1)
            .with_for_update()
        )
        row = await self._s.scalar(stmt)
        return _row_to_incident(row, with_children=False) if row is not None else None

    async def insert_incident(self, incident: Incident) -> None:
        self._s.add(_incident_to_row(incident))
        try:
            await self._s.flush()
        except IntegrityError as exc:
            await self._s.rollback()
            raise DuplicateActiveIncidentError(incident.correlation_key) from exc

    async def update_incident(self, incident: Incident) -> None:
        row = await self._s.get(IncidentRow, incident.id)
        if row is None:  # pragma: no cover - caller always has a real incident
            raise LookupError(incident.id)
        _apply_incident(row, incident)
        await self._s.flush()

    async def add_evidence(self, incident_id: str, evidence: EvidenceRecord) -> None:
        self._s.add(
            EvidenceRow(
                incident_id=incident_id,
                event_id=evidence.event_id,
                detector=evidence.detector,
                detector_version=evidence.detector_version,
                anomaly_score=evidence.anomaly_score,
                threshold=evidence.threshold,
                window_start=evidence.window_start,
                window_end=evidence.window_end,
                signals=dict(evidence.signals),
                abnormal_signals=list(evidence.abnormal_signals),
                trace_id=evidence.trace_id,
                occurred_at=evidence.occurred_at,
                received_at=datetime.now(tz=UTC),
                correlation_reason=evidence.correlation_reason,
            )
        )
        try:
            await self._s.flush()
        except IntegrityError as exc:  # event_id already present (race)
            await self._s.rollback()
            raise DuplicateActiveIncidentError(f"evidence {evidence.event_id}") from exc

    async def active_incidents_in_services(
        self, services: list[str], environment: str
    ) -> list[Incident]:
        if not services:
            return []
        rows = (
            await self._s.scalars(
                select(IncidentRow).where(
                    IncidentRow.service.in_(services),
                    IncidentRow.environment == environment,
                    IncidentRow.status.in_(_ACTIVE_SQL),
                )
            )
        ).all()
        return [_row_to_incident(r, with_children=False) for r in rows]

    async def link_incident(self, relation: IncidentRelation) -> None:
        exists = await self._s.scalar(
            select(IncidentRelationRow.incident_id).where(
                IncidentRelationRow.incident_id == relation.incident_id,
                IncidentRelationRow.related_incident_id == relation.related_incident_id,
            )
        )
        if exists is not None:
            return
        self._s.add(
            IncidentRelationRow(
                incident_id=relation.incident_id,
                related_incident_id=relation.related_incident_id,
                relation_type=str(relation.relation_type),
                reason=relation.reason,
                created_at=relation.created_at,
            )
        )
        try:
            await self._s.flush()
        except IntegrityError as exc:  # concurrent insert of the same edge
            await self._s.rollback()
            raise DuplicateActiveIncidentError(
                f"relation {relation.incident_id}->{relation.related_incident_id}"
            ) from exc

    async def add_transition(self, incident_id: str, transition: StateTransition) -> None:
        self._s.add(
            StateHistoryRow(
                incident_id=incident_id,
                from_status=str(transition.from_status) if transition.from_status else None,
                to_status=str(transition.to_status),
                actor=transition.actor,
                reason=transition.reason,
                severity_at_transition=(
                    str(transition.severity_at_transition)
                    if transition.severity_at_transition
                    else None
                ),
                created_at=transition.created_at,
            )
        )
        await self._s.flush()


class SqlIncidentRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    def unit_of_work(self) -> AbstractAsyncContextManager[IncidentUnitOfWork]:
        return self._unit_of_work()

    @asynccontextmanager
    async def _unit_of_work(self) -> AsyncIterator[IncidentUnitOfWork]:
        async with self._db.session() as session, session.begin():
            yield _SqlUoW(session)

    async def get_incident(self, incident_id: str) -> Incident | None:
        async with self._db.session() as session:
            row = await session.scalar(
                select(IncidentRow)
                .options(selectinload(IncidentRow.evidence), selectinload(IncidentRow.history))
                .where(IncidentRow.id == incident_id)
            )
            return _row_to_incident(row) if row is not None else None

    async def get_active_incident(self, correlation_key: str) -> Incident | None:
        async with self._db.session() as session:
            row = await session.scalar(
                select(IncidentRow)
                .where(
                    IncidentRow.correlation_key == correlation_key,
                    IncidentRow.status.in_(_ACTIVE_SQL),
                )
                .order_by(IncidentRow.last_evidence_at.desc())
                .limit(1)
            )
            return _row_to_incident(row, with_children=False) if row is not None else None

    async def get_related_incidents(self, incident_id: str) -> list[Incident]:
        async with self._db.session() as session:
            forward = await session.scalars(
                select(IncidentRelationRow.related_incident_id).where(
                    IncidentRelationRow.incident_id == incident_id
                )
            )
            backward = await session.scalars(
                select(IncidentRelationRow.incident_id).where(
                    IncidentRelationRow.related_incident_id == incident_id
                )
            )
            ids = set(forward.all()) | set(backward.all())
            if not ids:
                return []
            rows = (
                await session.scalars(
                    select(IncidentRow)
                    .where(IncidentRow.id.in_(ids))
                    .order_by(IncidentRow.created_at.desc())
                )
            ).all()
            return [_row_to_incident(r, with_children=False) for r in rows]

    async def link_incidents(
        self,
        incident_id: str,
        related_incident_id: str,
        relation_type: str,
        *,
        reason: str = "",
    ) -> None:
        async with self._db.session() as session, session.begin():
            uow = _SqlUoW(session)
            await uow.link_incident(
                IncidentRelation(
                    incident_id=incident_id,
                    related_incident_id=related_incident_id,
                    relation_type=IncidentRelationType(relation_type),
                    reason=reason,
                    created_at=datetime.now(tz=UTC),
                )
            )

    async def list_incidents(self, flt: IncidentFilter) -> list[Incident]:
        stmt = select(IncidentRow).order_by(IncidentRow.created_at.desc())
        if flt.status is not None:
            stmt = stmt.where(IncidentRow.status == str(flt.status))
        if flt.service is not None:
            stmt = stmt.where(IncidentRow.service == flt.service)
        if flt.severity is not None:
            stmt = stmt.where(IncidentRow.severity == str(flt.severity))
        if flt.since is not None:
            stmt = stmt.where(IncidentRow.created_at >= flt.since)
        stmt = stmt.limit(flt.limit).offset(flt.offset)
        async with self._db.session() as session:
            rows = (await session.scalars(stmt)).all()
            return [_row_to_incident(r, with_children=False) for r in rows]

    async def get_evidence(self, incident_id: str) -> list[EvidenceRecord] | None:
        inc = await self.get_incident(incident_id)
        return copy.deepcopy(inc.evidence) if inc is not None else None

    async def get_history(self, incident_id: str) -> list[StateTransition] | None:
        inc = await self.get_incident(incident_id)
        return copy.deepcopy(inc.history) if inc is not None else None

    async def apply_transition(
        self, incident_id: str, target: IncidentStatus, *, actor: str, reason: str
    ) -> Incident | None:
        """Manual (API-driven) lifecycle transition. Validation is the caller's job."""

        now = datetime.now(tz=UTC)
        async with self._db.session() as session, session.begin():
            row = await session.get(IncidentRow, incident_id, with_for_update=True)
            if row is None:
                return None
            prev = IncidentStatus(row.status)
            row.status = str(target)
            row.updated_at = now
            if target is IncidentStatus.ACKNOWLEDGED and row.acknowledged_at is None:
                row.acknowledged_at = now
            if target is IncidentStatus.RESOLVED:
                row.resolved_at = now
                row.resolution = row.resolution or "manual"
            session.add(
                StateHistoryRow(
                    incident_id=incident_id,
                    from_status=str(prev),
                    to_status=str(target),
                    actor=actor,
                    reason=reason,
                    severity_at_transition=row.severity,
                    created_at=now,
                )
            )
        return await self.get_incident(incident_id)

    async def health_check(self) -> bool:
        return await self._db.ping()
