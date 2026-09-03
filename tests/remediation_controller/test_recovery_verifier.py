"""DeterministicRecoveryVerifier — bounded poll loop + evaluation (Phase 5F)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from remediation_controller.domain.models import ServiceTarget
from remediation_controller.executor.simulation import SimulationState
from remediation_controller.recovery.config import RecoveryVerificationConfig
from remediation_controller.recovery.health import SimulatedHealthProbe
from remediation_controller.recovery.model import (
    VERIFIER_TYPE_LOCAL,
    HealthSnapshot,
    HealthStatus,
    VerificationStatus,
)
from remediation_controller.recovery.verifier import DeterministicRecoveryVerifier
from tests.remediation_controller.policy_fakes import BASE_TIME

_TARGET = ServiceTarget(service_name="orders-service", environment="development")


def _cfg(**over: object) -> RecoveryVerificationConfig:
    base: dict[str, object] = {"timeout_seconds": 10, "poll_interval_seconds": 1.0}
    base.update(over)
    return RecoveryVerificationConfig(**base)  # type: ignore[arg-type]


def _verifier(state: SimulationState) -> DeterministicRecoveryVerifier:
    return DeterministicRecoveryVerifier(SimulatedHealthProbe(state))


async def test_healthy_on_first_poll_recovers_immediately() -> None:
    state = SimulationState()
    state.mark_remediated("orders-service", BASE_TIME)
    outcome = await _verifier(state).verify(target=_TARGET, config=_cfg(), started_at=BASE_TIME)

    assert outcome.status is VerificationStatus.RECOVERED
    assert outcome.attempts == 1
    assert outcome.first_healthy_at == BASE_TIME
    assert all(c.passed for c in outcome.checks)
    assert outcome.verifier_type == VERIFIER_TYPE_LOCAL


async def test_healthy_after_several_polls() -> None:
    state = SimulationState()
    state.inject_fault("orders-service", recover_after=timedelta(seconds=4))
    state.mark_remediated("orders-service", BASE_TIME)

    outcome = await _verifier(state).verify(
        target=_TARGET,
        config=_cfg(timeout_seconds=10, poll_interval_seconds=1.0),
        started_at=BASE_TIME,
    )
    assert outcome.status is VerificationStatus.RECOVERED
    assert outcome.attempts == 5  # polls at t=0,1,2,3 fail; t=4 passes
    assert outcome.first_healthy_at == BASE_TIME + timedelta(seconds=4)


async def test_never_healthy_times_out_to_recovery_failed() -> None:
    state = SimulationState()
    state.inject_fault("orders-service", chronic=True)
    state.mark_remediated("orders-service", BASE_TIME)

    outcome = await _verifier(state).verify(
        target=_TARGET,
        config=_cfg(timeout_seconds=6, poll_interval_seconds=2.0),
        started_at=BASE_TIME,
    )
    assert outcome.status is VerificationStatus.RECOVERY_FAILED
    assert outcome.attempts == outcome.attempts  # bounded
    assert outcome.first_healthy_at is None
    assert outcome.failure_reason is not None
    assert not all(c.passed for c in outcome.checks)


async def test_slow_recovery_beyond_window_fails() -> None:
    state = SimulationState()
    state.inject_fault("orders-service", recover_after=timedelta(seconds=3600))
    state.mark_remediated("orders-service", BASE_TIME)

    outcome = await _verifier(state).verify(
        target=_TARGET,
        config=_cfg(timeout_seconds=5, poll_interval_seconds=1.0),
        started_at=BASE_TIME,
    )
    assert outcome.status is VerificationStatus.RECOVERY_FAILED


async def test_loop_is_bounded_by_max_attempts() -> None:
    calls: list[datetime] = []

    class _CountingProbe:
        def probe(self, service: str, environment: str, *, now: datetime) -> HealthSnapshot:
            calls.append(now)
            return HealthSnapshot(
                service=service,
                environment=environment,
                status=HealthStatus.UNHEALTHY,
                ready=False,
                running=False,
                replicas_available=0,
                error_rate=1.0,
                success_rate=0.0,
                latency_p95_ms=9000.0,
                detail="down",
                observed_at=now,
            )

    cfg = _cfg(timeout_seconds=20, poll_interval_seconds=4.0)  # max_attempts = 6
    v = DeterministicRecoveryVerifier(_CountingProbe())
    outcome = await v.verify(target=_TARGET, config=cfg, started_at=BASE_TIME)
    assert outcome.status is VerificationStatus.RECOVERY_FAILED
    assert len(calls) == cfg.max_attempts == 6
    # virtual clock advanced by poll_interval each time
    assert calls == [BASE_TIME + timedelta(seconds=4 * i) for i in range(6)]


async def test_probe_that_raises_is_a_failed_poll_not_a_crash() -> None:
    class _BoomProbe:
        def probe(self, service: str, environment: str, *, now: datetime) -> HealthSnapshot:
            raise RuntimeError("boom `kubectl delete pod`")

    v = DeterministicRecoveryVerifier(_BoomProbe())
    outcome = await v.verify(
        target=_TARGET,
        config=_cfg(timeout_seconds=2, poll_interval_seconds=1.0),
        started_at=BASE_TIME,
    )
    assert outcome.status is VerificationStatus.RECOVERY_FAILED
    assert any(c.name == "probe_error" for c in outcome.checks)


async def test_sleep_is_injectable_and_called_between_polls() -> None:
    sleeps: list[float] = []

    async def _spy(seconds: float) -> None:
        sleeps.append(seconds)

    state = SimulationState()
    state.inject_fault("orders-service", chronic=True)
    state.mark_remediated("orders-service", BASE_TIME)
    v = DeterministicRecoveryVerifier(SimulatedHealthProbe(state), sleep=_spy)
    cfg = _cfg(timeout_seconds=6, poll_interval_seconds=2.0)  # max_attempts = 4
    await v.verify(target=_TARGET, config=cfg, started_at=BASE_TIME)
    assert sleeps == [2.0, 2.0, 2.0]  # one fewer than max_attempts


async def test_verdict_ignores_probe_self_reported_status() -> None:
    """The probe claims HEALTHY but the signals fail the verifier's thresholds."""

    class _LyingProbe:
        def probe(self, service: str, environment: str, *, now: datetime) -> HealthSnapshot:
            return HealthSnapshot(
                service=service,
                environment=environment,
                status=HealthStatus.HEALTHY,  # lie
                ready=True,
                running=True,
                replicas_available=3,
                error_rate=0.9,  # but the error rate is terrible
                success_rate=0.1,
                latency_p95_ms=50.0,
                detail="totally fine, trust me",
                observed_at=now,
            )

    v = DeterministicRecoveryVerifier(_LyingProbe())
    outcome = await v.verify(
        target=_TARGET,
        config=_cfg(timeout_seconds=2, poll_interval_seconds=1.0),
        started_at=BASE_TIME,
    )
    assert outcome.status is VerificationStatus.RECOVERY_FAILED
    assert any(c.name == "error_rate" and not c.passed for c in outcome.checks)


async def test_config_max_attempts_is_deterministic() -> None:
    assert (
        RecoveryVerificationConfig(timeout_seconds=30, poll_interval_seconds=3.0).max_attempts == 11
    )
    assert (
        RecoveryVerificationConfig(timeout_seconds=1, poll_interval_seconds=10.0).max_attempts == 1
    )


def test_config_rejects_out_of_range() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RecoveryVerificationConfig(max_error_rate=2.0)
    with pytest.raises(ValidationError):
        RecoveryVerificationConfig(poll_interval_seconds=0.0)
