"""Deterministic anomaly-to-incident correlation (ADR-015).

Given an incoming :class:`~incident_correlator.domain.AnomalySignal` and the
single currently-active incident for its ``correlation_key`` (or ``None``), the
engine returns one of three decisions. It is a **pure function** — the
:mod:`incident_correlator.processor` applies it inside a database transaction.

Rules
-----
* ``correlation_key = "<service>:<environment>"`` — Phase 3 correlates by
  service. Finer correlation (metric family, dependency graph) is deferred.
* No active incident for the key            -> ``CREATE``.
* Active incident, and the anomaly is within ``CORRELATION_WINDOW_SECONDS`` of
  its last evidence                          -> ``APPEND``.
* Active incident, but the gap exceeds the window (it went quiet) -> ``SUPERSEDE``
  (auto-resolve the stale incident, open a fresh one).

Why 300 s by default: the telemetry cadence is ~10 s (Phase 2 scrape step). A
sustained problem emits an anomaly every ~10 s, so 300 s comfortably groups one
operational event while a genuinely new problem ten minutes later gets its own
incident.

Complexity: O(1) per anomaly — a single indexed lookup of the one active
incident, never a scan of incident history.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic_settings import BaseSettings, SettingsConfigDict

from incident_correlator.domain import AnomalySignal, Incident


class CorrelationConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CORRELATION_", env_file=".env", extra="ignore")

    window_seconds: float = 300.0


class CorrelationAction(StrEnum):
    CREATE = "CREATE"
    APPEND = "APPEND"
    SUPERSEDE = "SUPERSEDE"


@dataclass(frozen=True)
class CorrelationDecision:
    action: CorrelationAction
    reason: str


def decide(
    anomaly: AnomalySignal,
    active_incident: Incident | None,
    config: CorrelationConfig | None = None,
) -> CorrelationDecision:
    cfg = config or CorrelationConfig()

    if active_incident is None:
        return CorrelationDecision(CorrelationAction.CREATE, "no active incident for this service")

    gap = (anomaly.occurred_at - active_incident.last_evidence_at).total_seconds()
    if gap <= cfg.window_seconds:
        return CorrelationDecision(
            CorrelationAction.APPEND,
            f"within correlation window (gap {gap:.0f}s <= {cfg.window_seconds:.0f}s)",
        )
    return CorrelationDecision(
        CorrelationAction.SUPERSEDE,
        f"previous incident quiet for {gap:.0f}s (> {cfg.window_seconds:.0f}s); starting a new one",
    )
