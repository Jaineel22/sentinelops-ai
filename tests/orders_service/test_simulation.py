"""Failure-injection mechanism and its production guardrails."""

from __future__ import annotations

import random
import time

import pytest
from fastapi.testclient import TestClient

from orders_service.config import Settings, SimulationSettings
from orders_service.simulation import FailureInjector, SimulationError, SimulationState


def test_injection_is_disabled_by_default() -> None:
    injector = FailureInjector(SimulationSettings())
    assert injector.state.enabled is False
    injector.maybe_fail_request()  # does not raise
    injector.maybe_fail_publish()  # does not raise


def test_error_rate_one_always_fails() -> None:
    injector = FailureInjector(SimulationSettings(simulate_error_rate=1.0))
    with pytest.raises(SimulationError):
        injector.maybe_fail_request()


def test_publish_error_rate_one_always_fails() -> None:
    injector = FailureInjector(SimulationSettings(simulate_publish_error_rate=1.0))
    with pytest.raises(SimulationError):
        injector.maybe_fail_publish()


async def test_latency_injection_actually_delays() -> None:
    injector = FailureInjector(SimulationSettings(simulate_latency_ms=50))
    start = time.perf_counter()
    slept = await injector.apply_request_latency()
    assert slept == 50
    assert time.perf_counter() - start >= 0.045


def test_error_rate_is_probabilistic() -> None:
    injector = FailureInjector(SimulationSettings(simulate_error_rate=0.5), rng=random.Random(1234))
    failures = 0
    for _ in range(2000):
        try:
            injector.maybe_fail_request()
        except SimulationError:
            failures += 1
    assert 800 < failures < 1200


def test_production_refuses_to_start_with_injection_enabled() -> None:
    settings = Settings()
    settings.app.env = "production"
    settings.simulation.simulate_error_rate = 0.1
    with pytest.raises(RuntimeError, match="production"):
        settings.validate_for_env()


def test_production_refuses_sim_admin_endpoint() -> None:
    settings = Settings()
    settings.app.env = "production"
    settings.simulation.simulate_error_rate = 0.0
    settings.simulation.sim_admin_enabled = True
    with pytest.raises(RuntimeError, match="admin"):
        settings.validate_for_env()


def test_admin_endpoint_updates_runtime_state(client: TestClient) -> None:
    new_state = SimulationState(latency_ms=10, error_rate=0.0, publish_error_rate=0.0)
    response = client.put("/admin/simulation", json=new_state.model_dump())
    assert response.status_code == 200
    assert response.json()["latency_ms"] == 10
    assert client.get("/admin/simulation").json()["latency_ms"] == 10


def test_admin_endpoint_rejects_out_of_range_values(client: TestClient) -> None:
    assert client.put("/admin/simulation", json={"latency_ms": -1}).status_code == 422
    assert (
        client.put(
            "/admin/simulation",
            json={"latency_ms": 0, "error_rate": 2.0, "publish_error_rate": 0.0},
        ).status_code
        == 422
    )
