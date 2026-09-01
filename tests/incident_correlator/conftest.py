"""Fixtures for incident-correlator unit tests.

Most tests use :class:`InMemoryIncidentRepository` (no DB). ``sqlite_repo``
exercises the real SQLAlchemy code against a throwaway file SQLite database;
``-m integration`` tests use PostgreSQL (see ``test_sql_repository.py``).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from incident_correlator.db import Database, SqlIncidentRepository
from incident_correlator.domain import AnomalySignal
from incident_correlator.repository import InMemoryIncidentRepository

_BASE = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


def make_signal(
    *,
    service: str = "orders-service",
    environment: str = "development",
    offset_seconds: float = 0.0,
    window_seconds: float = 10.0,
    anomaly_score: float = 0.9,
    threshold: float = 0.5,
    signals: dict[str, float] | None = None,
    abnormal_signals: list[str] | None = None,
    event_id: str | None = None,
) -> AnomalySignal:
    start = _BASE + timedelta(seconds=offset_seconds)
    end = start + timedelta(seconds=window_seconds)
    return AnomalySignal(
        event_id=event_id or str(uuid.uuid4()),
        detector="isolation_forest",
        detector_version="0.2.0",
        service=service,
        environment=environment,
        window_start=start,
        window_end=end,
        anomaly_score=anomaly_score,
        threshold=threshold,
        signals=signals or {"error_rate": 0.0, "latency_p95_ms": 40.0, "request_rate": 3.0},
        abnormal_signals=abnormal_signals or [],
        trace_id="a" * 32,
        occurred_at=end,
    )


@pytest.fixture
def signal_factory() -> Callable[..., AnomalySignal]:
    return make_signal


@pytest.fixture
def repo() -> InMemoryIncidentRepository:
    return InMemoryIncidentRepository()


@pytest_asyncio.fixture
async def sqlite_repo(tmp_path: Path) -> AsyncIterator[SqlIncidentRepository]:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'incidents.db'}")
    await db.create_all()
    try:
        yield SqlIncidentRepository(db)
    finally:
        await db.dispose()
