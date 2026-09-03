"""Health probes for recovery verification (Phase 5F).

A :class:`HealthProbe` answers one question deterministically: *what does the
target service's health look like right now?* — as a structured
:class:`HealthSnapshot`. It never runs a command, opens a socket, or calls any
infrastructure.

The only probe in Phase 5F is :class:`SimulatedHealthProbe`, which reads the
executor's in-process :class:`~remediation_controller.executor.simulation.SimulationState`
and models a **deterministic post-remediation recovery trajectory**: a service
that has had a remediation applied converges to healthy exactly
``recovery_delay`` after the execution, unless it is ``chronic_fault`` (the
remediation did not address the root cause) — in which case it never recovers.

A real-infrastructure probe (HTTP ``/health``, metrics scrape) is a deliberate
future item behind this same Protocol.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from remediation_controller.executor.simulation import SimulationState
from remediation_controller.recovery.model import HealthSnapshot, HealthStatus

_ZERO = timedelta(0)


class HealthProbe(Protocol):
    """A deterministic, side-effect-free health observation."""

    def probe(self, service: str, environment: str, *, now: datetime) -> HealthSnapshot: ...


class SimulatedHealthProbe:
    """Reads a :class:`SimulationState`; never touches anything real."""

    def __init__(self, state: SimulationState) -> None:
        self._state = state

    def probe(self, service: str, environment: str, *, now: datetime) -> HealthSnapshot:
        s = self._state.service(service)

        if not s.running or s.replicas < 1:
            return self._snap(
                service,
                environment,
                now,
                HealthStatus.UNHEALTHY,
                ready=False,
                running=s.running,
                replicas=s.replicas,
                error_rate=1.0,
                latency=8000.0,
                detail="target service is not running or has no replicas",
            )

        if s.chronic_fault:
            return self._snap(
                service,
                environment,
                now,
                HealthStatus.DEGRADED,
                ready=False,
                running=True,
                replicas=s.replicas,
                error_rate=0.37,
                latency=2100.0,
                detail="service still failing after remediation; root cause not addressed",
            )

        if s.recovery_started_at is None:
            if s.faulted:
                return self._snap(
                    service,
                    environment,
                    now,
                    HealthStatus.DEGRADED,
                    ready=False,
                    running=True,
                    replicas=s.replicas,
                    error_rate=0.42,
                    latency=2600.0,
                    detail="incident active; no remediation applied yet",
                )
            return self._snap(
                service,
                environment,
                now,
                HealthStatus.HEALTHY,
                ready=True,
                running=True,
                replicas=s.replicas,
                error_rate=0.002,
                latency=110.0,
                detail="nominal",
            )

        elapsed = max(_ZERO, now - s.recovery_started_at)
        if elapsed >= s.recovery_delay:
            return self._snap(
                service,
                environment,
                now,
                HealthStatus.HEALTHY,
                ready=True,
                running=True,
                replicas=s.replicas,
                error_rate=0.003,
                latency=135.0,
                detail=f"recovered {elapsed.total_seconds():.1f}s after remediation",
            )

        frac = elapsed / s.recovery_delay if s.recovery_delay.total_seconds() > 0 else 1.0
        error_rate = round(0.30 * (1.0 - frac) + 0.01, 4)
        return self._snap(
            service,
            environment,
            now,
            HealthStatus.DEGRADED,
            ready=False,
            running=True,
            replicas=s.replicas,
            error_rate=error_rate,
            latency=round(1600.0 * (1.0 - frac) + 150.0, 1),
            detail=f"converging ({frac * 100:.0f}% through the expected recovery window)",
        )

    @staticmethod
    def _snap(
        service: str,
        environment: str,
        now: datetime,
        status: HealthStatus,
        *,
        ready: bool,
        running: bool,
        replicas: int,
        error_rate: float,
        latency: float,
        detail: str,
    ) -> HealthSnapshot:
        return HealthSnapshot(
            service=service,
            environment=environment,
            status=status,
            ready=ready,
            running=running,
            replicas_available=max(0, replicas),
            error_rate=error_rate,
            success_rate=round(1.0 - error_rate, 4),
            latency_p95_ms=latency,
            detail=detail,
            observed_at=now,
        )


__all__ = ["HealthProbe", "SimulatedHealthProbe"]
