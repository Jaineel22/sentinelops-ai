"""The recovery verifier (Phase 5F).

``RecoveryVerifier.verify`` runs a **bounded, deterministic poll loop** against a
:class:`~remediation_controller.recovery.health.HealthProbe`, evaluates each
observation against the verifier's *own* thresholds
(:class:`~remediation_controller.recovery.config.RecoveryVerificationConfig`),
and returns a :class:`~remediation_controller.recovery.model.RecoveryOutcome`
(``RECOVERED`` iff every check passes on some poll within the window, else
``RECOVERY_FAILED``).

Determinism / termination:

* the loop runs at most ``config.max_attempts`` times, then stops;
* a **virtual clock** starts at ``started_at`` and advances by
  ``poll_interval_seconds`` each iteration — health is evaluated at
  ``started_at``, ``started_at + interval``, … so the result is a pure function
  of ``(probe state, started_at, config)``;
* in the local simulation there is nothing real to wait on, so the loop does not
  sleep (an injected ``sleep`` — used by a future real-infrastructure verifier —
  paces it without changing the verdict);
* a probe that raises is treated as a failed poll (recorded as a redacted
  ``probe_error`` check), never as an execution path.

No LLM. No I/O. No command anywhere.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Protocol

from remediation_controller.audit.redaction import redact_text
from remediation_controller.domain.models import ServiceTarget
from remediation_controller.recovery.config import RecoveryVerificationConfig
from remediation_controller.recovery.health import HealthProbe, SimulatedHealthProbe
from remediation_controller.recovery.model import (
    VERIFIER_TYPE_LOCAL,
    VERIFIER_VERSION,
    HealthSnapshot,
    HealthStatus,
    RecoveryCheck,
    RecoveryOutcome,
    VerificationStatus,
)

Sleeper = Callable[[float], Awaitable[None]]


async def _no_sleep(_seconds: float) -> None:  # pragma: no cover - trivial
    return None


class RecoveryVerifier(Protocol):
    verifier_type: str
    verifier_version: str

    async def verify(
        self,
        *,
        target: ServiceTarget,
        config: RecoveryVerificationConfig,
        started_at: datetime,
    ) -> RecoveryOutcome: ...


class DeterministicRecoveryVerifier:
    """The only Phase 5F verifier — deterministic, in-process, LLM-free."""

    verifier_type: str = VERIFIER_TYPE_LOCAL
    verifier_version: str = VERIFIER_VERSION

    def __init__(self, probe: HealthProbe, *, sleep: Sleeper | None = None) -> None:
        self._probe = probe
        self._sleep = sleep or _no_sleep

    async def verify(
        self,
        *,
        target: ServiceTarget,
        config: RecoveryVerificationConfig,
        started_at: datetime,
    ) -> RecoveryOutcome:
        step = timedelta(seconds=config.poll_interval_seconds)
        current = started_at
        last_checks: tuple[RecoveryCheck, ...] = ()
        last_probe_status = HealthStatus.UNHEALTHY
        last_detail = "no health observation was obtained"

        for attempt in range(1, config.max_attempts + 1):
            snapshot = self._observe(target, current)
            if snapshot is not None:
                last_probe_status = snapshot.status
                last_detail = snapshot.detail
                last_checks = _evaluate(snapshot, config)
            else:
                last_probe_status = HealthStatus.UNHEALTHY
                last_detail = "health probe raised while observing the target"
                last_checks = (_probe_error_check(last_detail),)

            if all(c.passed for c in last_checks):
                return RecoveryOutcome(
                    status=VerificationStatus.RECOVERED,
                    checks=last_checks,
                    attempts=attempt,
                    first_healthy_at=current,
                    failure_reason=None,
                    probe_status=last_probe_status,
                    verifier_type=self.verifier_type,
                    verifier_version=self.verifier_version,
                    started_at=started_at,
                    completed_at=current,
                )

            if attempt < config.max_attempts:
                await self._sleep(config.poll_interval_seconds)
                current = current + step

        failed = [c.name for c in last_checks if not c.passed]
        # ``last_detail`` is untrusted data from the monitored service — it is
        # redacted and recorded for a human reader, never parsed or executed.
        reason = (
            f"recovery not observed within {config.timeout_seconds}s "
            f"({config.max_attempts} polls); failing checks: {', '.join(failed) or 'none'}; "
            f"last health detail: {redact_text(last_detail)[:300]}"
        )
        return RecoveryOutcome(
            status=VerificationStatus.RECOVERY_FAILED,
            checks=last_checks,
            attempts=config.max_attempts,
            first_healthy_at=None,
            failure_reason=redact_text(reason),
            probe_status=last_probe_status,
            verifier_type=self.verifier_type,
            verifier_version=self.verifier_version,
            started_at=started_at,
            completed_at=current,
        )

    def _observe(self, target: ServiceTarget, now: datetime) -> HealthSnapshot | None:
        try:
            return self._probe.probe(target.service_name, target.environment, now=now)
        except Exception:
            # A probe failure is a failed poll, never a raise — the untrusted
            # target must not be able to break the verification loop.
            return None


def _evaluate(
    snapshot: HealthSnapshot, config: RecoveryVerificationConfig
) -> tuple[RecoveryCheck, ...]:
    """The verifier's own deterministic thresholds — it never trusts the probe's
    self-reported ``status`` for the verdict."""

    checks = [
        _check(
            "service_running",
            passed=snapshot.running,
            observed=str(snapshot.running),
            threshold="running is True",
            detail="the target service process is up",
        ),
        _check(
            "error_rate",
            passed=snapshot.error_rate <= config.max_error_rate,
            observed=f"{snapshot.error_rate:.4f}",
            threshold=f"<= {config.max_error_rate}",
            detail="request error rate is within the recovery threshold",
        ),
        _check(
            "latency_p95",
            passed=snapshot.latency_p95_ms <= config.max_latency_p95_ms,
            observed=f"{snapshot.latency_p95_ms:.1f}ms",
            threshold=f"<= {config.max_latency_p95_ms}ms",
            detail="p95 latency is within the recovery threshold",
        ),
    ]
    if config.require_ready:
        checks.append(
            _check(
                "readiness",
                passed=snapshot.ready,
                observed=str(snapshot.ready),
                threshold="ready is True",
                detail="the target service reports ready",
            )
        )
    return tuple(checks)


def _check(name: str, *, passed: bool, observed: str, threshold: str, detail: str) -> RecoveryCheck:
    return RecoveryCheck(
        name=name,
        passed=passed,
        observed=redact_text(observed)[:200],
        threshold=threshold[:200],
        detail=redact_text(detail)[:500],
    )


def _probe_error_check(message: str) -> RecoveryCheck:
    return RecoveryCheck(
        name="probe_error",
        passed=False,
        observed="error",
        threshold="probe returns a snapshot",
        detail=redact_text(message)[:500],
    )


def build_default_verifier(probe_source: object) -> DeterministicRecoveryVerifier:
    """Build the default local verifier from whatever the executor exposes as its
    simulation ``state`` (fail-safe to a fresh empty state)."""

    from remediation_controller.executor.simulation import SimulationState

    state = getattr(probe_source, "state", None)
    if not isinstance(state, SimulationState):
        state = SimulationState()
    return DeterministicRecoveryVerifier(SimulatedHealthProbe(state))


__all__ = [
    "DeterministicRecoveryVerifier",
    "RecoveryVerifier",
    "Sleeper",
    "build_default_verifier",
]
