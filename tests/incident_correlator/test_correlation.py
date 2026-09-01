"""Deterministic anomaly-to-incident correlation decision (ADR-015)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from incident_correlator.correlation import CorrelationAction, CorrelationConfig, decide
from incident_correlator.domain import (
    AnomalySignal,
    Incident,
    IncidentStatus,
    Severity,
    correlation_key,
)


def _incident(
    *, last_evidence_at: datetime, status: IncidentStatus = IncidentStatus.OPEN
) -> Incident:
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    return Incident(
        id="inc_1",
        correlation_key=correlation_key("orders-service", "development"),
        service="orders-service",
        environment="development",
        status=status,
        severity=Severity.LOW,
        severity_reasons=[],
        title="t",
        anomaly_count=1,
        max_anomaly_score=0.9,
        max_error_rate=0.0,
        max_latency_p95_ms=0.0,
        detector="isolation_forest",
        started_at=now,
        last_evidence_at=last_evidence_at,
        created_at=now,
        updated_at=now,
    )


def test_no_active_incident_creates(signal_factory: Callable[..., AnomalySignal]) -> None:
    d = decide(signal_factory(), None)
    assert d.action is CorrelationAction.CREATE


def test_within_window_appends(signal_factory: Callable[..., AnomalySignal]) -> None:
    sig = signal_factory(offset_seconds=120)
    incident = _incident(last_evidence_at=sig.occurred_at - timedelta(seconds=90))
    d = decide(sig, incident, CorrelationConfig(window_seconds=300))
    assert d.action is CorrelationAction.APPEND
    assert "within correlation window" in d.reason


def test_outside_window_supersedes(signal_factory: Callable[..., AnomalySignal]) -> None:
    sig = signal_factory(offset_seconds=1000)
    incident = _incident(last_evidence_at=sig.occurred_at - timedelta(seconds=600))
    d = decide(sig, incident, CorrelationConfig(window_seconds=300))
    assert d.action is CorrelationAction.SUPERSEDE


def test_window_boundary_is_inclusive(signal_factory: Callable[..., AnomalySignal]) -> None:
    sig = signal_factory()
    incident = _incident(last_evidence_at=sig.occurred_at - timedelta(seconds=300))
    assert decide(sig, incident, CorrelationConfig(window_seconds=300)).action is (
        CorrelationAction.APPEND
    )


def test_window_is_configurable(signal_factory: Callable[..., AnomalySignal]) -> None:
    sig = signal_factory()
    incident = _incident(last_evidence_at=sig.occurred_at - timedelta(seconds=60))
    assert decide(sig, incident, CorrelationConfig(window_seconds=30)).action is (
        CorrelationAction.SUPERSEDE
    )
