"""SimulatedHealthProbe — deterministic post-remediation health trajectory (Phase 5F)."""

from __future__ import annotations

from datetime import timedelta

from remediation_controller.executor.simulation import SimulationState
from remediation_controller.recovery.health import SimulatedHealthProbe
from remediation_controller.recovery.model import HealthStatus
from tests.remediation_controller.policy_fakes import BASE_TIME

_SVC = "orders-service"
_ENV = "development"


def _probe(state: SimulationState) -> SimulatedHealthProbe:
    return SimulatedHealthProbe(state)


def test_nominal_service_is_healthy() -> None:
    snap = _probe(SimulationState()).probe(_SVC, _ENV, now=BASE_TIME)
    assert snap.status is HealthStatus.HEALTHY
    assert snap.ready is True and snap.running is True
    assert snap.error_rate <= 0.01
    assert snap.success_rate >= 0.99


def test_stopped_service_is_unhealthy() -> None:
    state = SimulationState()
    state.service(_SVC).running = False
    snap = _probe(state).probe(_SVC, _ENV, now=BASE_TIME)
    assert snap.status is HealthStatus.UNHEALTHY
    assert snap.ready is False
    assert snap.error_rate == 1.0


def test_zero_replicas_is_unhealthy() -> None:
    state = SimulationState()
    state.service(_SVC).replicas = 0
    snap = _probe(state).probe(_SVC, _ENV, now=BASE_TIME)
    assert snap.status is HealthStatus.UNHEALTHY


def test_faulted_service_without_remediation_is_degraded() -> None:
    state = SimulationState()
    state.inject_fault(_SVC)
    snap = _probe(state).probe(_SVC, _ENV, now=BASE_TIME)
    assert snap.status is HealthStatus.DEGRADED
    assert snap.ready is False
    assert snap.error_rate > 0.05


def test_service_converges_to_healthy_after_recovery_delay() -> None:
    state = SimulationState()
    state.inject_fault(_SVC, recover_after=timedelta(seconds=10))
    state.mark_remediated(_SVC, BASE_TIME)

    at_start = _probe(state).probe(_SVC, _ENV, now=BASE_TIME)
    assert at_start.status is HealthStatus.DEGRADED

    mid = _probe(state).probe(_SVC, _ENV, now=BASE_TIME + timedelta(seconds=5))
    assert mid.status is HealthStatus.DEGRADED
    assert mid.error_rate < at_start.error_rate  # converging

    done = _probe(state).probe(_SVC, _ENV, now=BASE_TIME + timedelta(seconds=10))
    assert done.status is HealthStatus.HEALTHY
    assert done.ready is True


def test_default_recovery_delay_is_immediate() -> None:
    state = SimulationState()
    state.mark_remediated(_SVC, BASE_TIME)
    snap = _probe(state).probe(_SVC, _ENV, now=BASE_TIME)
    assert snap.status is HealthStatus.HEALTHY


def test_chronic_fault_never_recovers() -> None:
    state = SimulationState()
    state.inject_fault(_SVC, chronic=True)
    state.mark_remediated(_SVC, BASE_TIME)
    for offset in (0, 60, 3600, 86_400):
        snap = _probe(state).probe(_SVC, _ENV, now=BASE_TIME + timedelta(seconds=offset))
        assert snap.status is HealthStatus.DEGRADED
        assert snap.ready is False


def test_probe_is_deterministic() -> None:
    state = SimulationState()
    state.inject_fault(_SVC, recover_after=timedelta(seconds=8))
    state.mark_remediated(_SVC, BASE_TIME)
    t = BASE_TIME + timedelta(seconds=3)
    a = _probe(state).probe(_SVC, _ENV, now=t)
    b = _probe(state).probe(_SVC, _ENV, now=t)
    assert a.model_dump() == b.model_dump()
