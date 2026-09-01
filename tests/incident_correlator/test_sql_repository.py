"""SqlIncidentRepository against a throwaway SQLite DB (fast) + Postgres locking
under ``-m integration``.

The processor logic is already covered against the in-memory repo; here we pin
that the *SQL* mapping, the unique index, the dedup constraint and the filters
behave the same.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable

import pytest
import pytest_asyncio

from incident_correlator.correlation import CorrelationConfig
from incident_correlator.db import Database, SqlIncidentRepository
from incident_correlator.domain import AnomalySignal, IncidentStatus
from incident_correlator.processor import AnomalyProcessor, ProcessResult
from incident_correlator.repository import (
    DuplicateActiveIncidentError,
    IncidentFilter,
    InMemoryIncidentRepository,
)


async def _proc(repo: object) -> AnomalyProcessor:
    return AnomalyProcessor(
        repo,  # type: ignore[arg-type]
        correlation_config=CorrelationConfig(window_seconds=300),
    )


async def test_create_and_read_back(
    sqlite_repo: SqlIncidentRepository, signal_factory: Callable[..., AnomalySignal]
) -> None:
    proc = await _proc(sqlite_repo)
    out = await proc.process(signal_factory(abnormal_signals=["latency_p95_ms"]))
    assert out.result is ProcessResult.CREATED

    inc = await sqlite_repo.get_incident(out.incident_id or "")
    assert inc is not None
    assert inc.status is IncidentStatus.OPEN
    assert inc.anomaly_count == 1
    assert len(inc.evidence) == 1
    assert inc.evidence[0].abnormal_signals == ["latency_p95_ms"]
    assert len(inc.history) == 1 and inc.history[0].to_status is IncidentStatus.OPEN


async def test_append_and_dedup(
    sqlite_repo: SqlIncidentRepository, signal_factory: Callable[..., AnomalySignal]
) -> None:
    proc = await _proc(sqlite_repo)
    await proc.process(signal_factory(event_id="a", offset_seconds=0))
    await proc.process(signal_factory(event_id="b", offset_seconds=10))
    dup = await proc.process(signal_factory(event_id="b", offset_seconds=10))
    assert dup.result is ProcessResult.DUPLICATE

    incidents = await sqlite_repo.list_incidents(IncidentFilter())
    assert len(incidents) == 1
    inc = await sqlite_repo.get_incident(incidents[0].id)
    assert inc is not None and inc.anomaly_count == 2 and len(inc.evidence) == 2


async def test_partial_unique_index_blocks_second_active_incident(
    sqlite_repo: SqlIncidentRepository, signal_factory: Callable[..., AnomalySignal]
) -> None:
    from incident_correlator.correlation import (
        CorrelationAction,
        CorrelationDecision,
    )

    proc = await _proc(sqlite_repo)
    await proc.process(signal_factory(event_id="a"))  # first active incident for the key

    incident, _ev, _tr = proc._new_incident(
        signal_factory(event_id="c"), CorrelationDecision(CorrelationAction.CREATE, "test")
    )
    with pytest.raises(DuplicateActiveIncidentError):
        async with sqlite_repo.unit_of_work() as uow:
            await uow.insert_incident(incident)


async def test_filters(
    sqlite_repo: SqlIncidentRepository, signal_factory: Callable[..., AnomalySignal]
) -> None:
    proc = await _proc(sqlite_repo)
    await proc.process(signal_factory(service="orders-service", event_id="1"))
    await proc.process(signal_factory(service="payments-service", event_id="2"))

    by_service = await sqlite_repo.list_incidents(IncidentFilter(service="orders-service"))
    assert len(by_service) == 1 and by_service[0].service == "orders-service"

    open_ones = await sqlite_repo.list_incidents(IncidentFilter(status=IncidentStatus.OPEN))
    assert len(open_ones) == 2


async def test_manual_transition_and_history(
    sqlite_repo: SqlIncidentRepository, signal_factory: Callable[..., AnomalySignal]
) -> None:
    proc = await _proc(sqlite_repo)
    out = await proc.process(signal_factory())
    incident_id = out.incident_id or ""

    updated = await sqlite_repo.apply_transition(
        incident_id, IncidentStatus.ACKNOWLEDGED, actor="api", reason="triaged"
    )
    assert updated is not None and updated.status is IncidentStatus.ACKNOWLEDGED
    assert updated.acknowledged_at is not None

    resolved = await sqlite_repo.apply_transition(
        incident_id, IncidentStatus.RESOLVED, actor="api", reason="fixed"
    )
    assert resolved is not None and resolved.status is IncidentStatus.RESOLVED
    assert resolved.resolution == "manual"
    assert [h.to_status for h in resolved.history] == [
        IncidentStatus.OPEN,
        IncidentStatus.ACKNOWLEDGED,
        IncidentStatus.RESOLVED,
    ]

    # key is now free for a new incident
    fresh = await proc.process(signal_factory(event_id="new", offset_seconds=1000))
    assert fresh.result is ProcessResult.CREATED


async def test_in_memory_and_sql_agree_on_correlation(
    sqlite_repo: SqlIncidentRepository, signal_factory: Callable[..., AnomalySignal]
) -> None:
    mem = InMemoryIncidentRepository()
    for repo in (mem, sqlite_repo):
        proc = await _proc(repo)
        await proc.process(signal_factory(event_id=f"{id(repo)}-1", offset_seconds=0))
        await proc.process(signal_factory(event_id=f"{id(repo)}-2", offset_seconds=15))
    mem_inc = (await mem.list_incidents(IncidentFilter()))[0]
    sql_inc = (await sqlite_repo.list_incidents(IncidentFilter()))[0]
    assert mem_inc.anomaly_count == sql_inc.anomaly_count == 2
    assert mem_inc.severity == sql_inc.severity


# --- PostgreSQL only: true row locking / concurrent writers ------------
_PG_URL = os.environ.get("DB_TEST_URL")
pg = pytest.mark.skipif(_PG_URL is None, reason="set DB_TEST_URL to a Postgres DB")


@pytest_asyncio.fixture
async def pg_repo() -> AsyncIterator[SqlIncidentRepository]:
    assert _PG_URL is not None
    db = Database(_PG_URL)
    await db.create_all()
    try:
        async with db.engine.begin() as conn:
            from incident_correlator.db.models import Base

            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        yield SqlIncidentRepository(db)
    finally:
        await db.dispose()


@pytest.mark.integration
@pg
async def test_concurrent_first_anomalies_make_one_incident(
    pg_repo: SqlIncidentRepository, signal_factory: Callable[..., AnomalySignal]
) -> None:
    import asyncio

    proc = AnomalyProcessor(pg_repo, correlation_config=CorrelationConfig(window_seconds=300))
    results = await asyncio.gather(
        proc.process(signal_factory(event_id="p1", offset_seconds=0)),
        proc.process(signal_factory(event_id="p2", offset_seconds=1)),
        proc.process(signal_factory(event_id="p3", offset_seconds=2)),
    )
    incidents = await pg_repo.list_incidents(IncidentFilter())
    assert len(incidents) == 1
    assert incidents[0].anomaly_count == 3
    assert sum(1 for r in results if r.result is ProcessResult.CREATED) == 1
