"""Persistence boundary for incidents.

The processor never touches SQLAlchemy directly; it goes through
:class:`IncidentRepository`. A *unit of work* groups the dedupe check, the
active-incident lock, and all writes into one atomic commit — so a crash
mid-processing leaves no partial incident and Kafka replays the message
(at-least-once + idempotent, ADR-016).

* :class:`InMemoryIncidentRepository` — for unit tests (implements the same
  atomicity + "one active incident per key" invariant in Python).
* ``incident_correlator.db.SqlIncidentRepository`` — PostgreSQL.
"""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from incident_correlator.domain import (
    ACTIVE_STATUSES,
    EvidenceRecord,
    Incident,
    IncidentStatus,
    Severity,
    StateTransition,
)


class DuplicateActiveIncidentError(RuntimeError):
    """Two active incidents for one correlation_key — the DB unique index (or the
    in-memory equivalent) rejected a concurrent create. The caller retries."""


@dataclass(frozen=True)
class IncidentFilter:
    status: IncidentStatus | None = None
    service: str | None = None
    severity: Severity | None = None
    since: datetime | None = None
    limit: int = 50
    offset: int = 0


class IncidentUnitOfWork(Protocol):
    async def evidence_exists(self, event_id: str) -> bool: ...

    async def lock_active_incident(self, correlation_key: str) -> Incident | None: ...

    async def insert_incident(self, incident: Incident) -> None: ...

    async def update_incident(self, incident: Incident) -> None: ...

    async def add_evidence(self, incident_id: str, evidence: EvidenceRecord) -> None: ...

    async def add_transition(self, incident_id: str, transition: StateTransition) -> None: ...


class IncidentRepository(Protocol):
    def unit_of_work(self) -> AbstractAsyncContextManager[IncidentUnitOfWork]: ...

    async def get_incident(self, incident_id: str) -> Incident | None: ...

    async def get_active_incident(self, correlation_key: str) -> Incident | None: ...

    async def list_incidents(self, flt: IncidentFilter) -> list[Incident]: ...

    async def get_evidence(self, incident_id: str) -> list[EvidenceRecord] | None: ...

    async def get_history(self, incident_id: str) -> list[StateTransition] | None: ...

    async def apply_transition(
        self, incident_id: str, target: IncidentStatus, *, actor: str, reason: str
    ) -> Incident | None: ...

    async def health_check(self) -> bool: ...


# --- in-memory implementation ------------------------------------------
class _InMemoryUoW:
    def __init__(self, store: _Store) -> None:
        self._store = store
        self._ops: list[Callable[[], None]] = []
        # snapshot the evidence-id set so dedupe sees staged inserts too
        self._staged_event_ids: set[str] = set()

    async def evidence_exists(self, event_id: str) -> bool:
        return event_id in self._store.evidence_ids or event_id in self._staged_event_ids

    async def lock_active_incident(self, correlation_key: str) -> Incident | None:
        for inc in self._store.incidents.values():
            if inc.correlation_key == correlation_key and inc.status in ACTIVE_STATUSES:
                return copy.deepcopy(inc)
        return None

    async def insert_incident(self, incident: Incident) -> None:
        key = incident.correlation_key
        snapshot = copy.deepcopy(incident)
        snapshot.evidence = []  # children are inserted via add_evidence / add_transition
        snapshot.history = []

        def _apply() -> None:
            if any(
                i.correlation_key == key and i.status in ACTIVE_STATUSES
                for i in self._store.incidents.values()
            ):
                raise DuplicateActiveIncidentError(key)
            self._store.incidents[snapshot.id] = snapshot

        self._ops.append(_apply)

    async def update_incident(self, incident: Incident) -> None:
        """Update the incident row only; evidence/history rows are untouched."""

        snapshot = copy.deepcopy(incident)

        def _apply() -> None:
            stored = self._store.incidents[snapshot.id]
            snapshot.evidence = stored.evidence
            snapshot.history = stored.history
            self._store.incidents[snapshot.id] = snapshot

        self._ops.append(_apply)

    async def add_evidence(self, incident_id: str, evidence: EvidenceRecord) -> None:
        self._staged_event_ids.add(evidence.event_id)
        snapshot = copy.deepcopy(evidence)

        def _apply() -> None:
            self._store.incidents[incident_id].evidence.append(snapshot)
            self._store.evidence_ids.add(snapshot.event_id)

        self._ops.append(_apply)

    async def add_transition(self, incident_id: str, transition: StateTransition) -> None:
        snapshot = copy.deepcopy(transition)
        self._ops.append(lambda: self._store.incidents[incident_id].history.append(snapshot))

    def commit(self) -> None:
        for op in self._ops:
            op()


@dataclass
class _Store:
    incidents: dict[str, Incident]
    evidence_ids: set[str]


class InMemoryIncidentRepository:
    def __init__(self) -> None:
        self._store = _Store(incidents={}, evidence_ids=set())

    @asynccontextmanager
    async def unit_of_work(self) -> AsyncIterator[IncidentUnitOfWork]:
        uow = _InMemoryUoW(self._store)
        yield uow
        uow.commit()  # only reached if the body did not raise

    async def get_incident(self, incident_id: str) -> Incident | None:
        inc = self._store.incidents.get(incident_id)
        return copy.deepcopy(inc) if inc else None

    async def get_active_incident(self, correlation_key: str) -> Incident | None:
        for inc in self._store.incidents.values():
            if inc.correlation_key == correlation_key and inc.status in ACTIVE_STATUSES:
                return copy.deepcopy(inc)
        return None

    async def list_incidents(self, flt: IncidentFilter) -> list[Incident]:
        rows = [copy.deepcopy(i) for i in self._store.incidents.values()]
        if flt.status is not None:
            rows = [i for i in rows if i.status == flt.status]
        if flt.service is not None:
            rows = [i for i in rows if i.service == flt.service]
        if flt.severity is not None:
            rows = [i for i in rows if i.severity == flt.severity]
        if flt.since is not None:
            rows = [i for i in rows if i.created_at >= flt.since]
        rows.sort(key=lambda i: i.created_at, reverse=True)
        return rows[flt.offset : flt.offset + flt.limit]

    async def get_evidence(self, incident_id: str) -> list[EvidenceRecord] | None:
        inc = self._store.incidents.get(incident_id)
        return copy.deepcopy(inc.evidence) if inc else None

    async def get_history(self, incident_id: str) -> list[StateTransition] | None:
        inc = self._store.incidents.get(incident_id)
        return copy.deepcopy(inc.history) if inc else None

    async def apply_transition(
        self, incident_id: str, target: IncidentStatus, *, actor: str, reason: str
    ) -> Incident | None:
        inc = self._store.incidents.get(incident_id)
        if inc is None:
            return None
        now = datetime.now(tz=UTC)
        prev = inc.status
        inc.status = target
        inc.updated_at = now
        if target is IncidentStatus.ACKNOWLEDGED and inc.acknowledged_at is None:
            inc.acknowledged_at = now
        if target is IncidentStatus.RESOLVED:
            inc.resolved_at = now
            inc.resolution = inc.resolution or "manual"
        inc.history.append(
            StateTransition(
                from_status=prev,
                to_status=target,
                actor=actor,
                reason=reason,
                severity_at_transition=inc.severity,
                created_at=now,
            )
        )
        return copy.deepcopy(inc)

    async def health_check(self) -> bool:
        return True
