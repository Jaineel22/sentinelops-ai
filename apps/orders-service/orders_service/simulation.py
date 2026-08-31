"""Development-only failure injection.

Purpose: later phases need to see what *abnormal* telemetry looks like. This
module lets a developer deliberately add latency, HTTP errors, or Kafka publish
failures so those scenarios can be reproduced on demand.

Safety properties:

* Disabled by default (all knobs 0).
* Never silently active in production — ``Settings.validate_for_env`` refuses to
  start the process if a knob is set while ``APP_ENV=production``.
* Only three bounded numeric knobs. No shell, no arbitrary code, no infra calls.
* Runtime changes go through the dev-only ``/admin/simulation`` endpoint, which
  is not mounted in production.
"""

from __future__ import annotations

import asyncio
import random

from pydantic import BaseModel, Field

from orders_service.config import SimulationSettings


class SimulationState(BaseModel):
    latency_ms: int = Field(ge=0, le=60_000)
    error_rate: float = Field(ge=0.0, le=1.0)
    publish_error_rate: float = Field(ge=0.0, le=1.0)

    @property
    def enabled(self) -> bool:
        return self.latency_ms > 0 or self.error_rate > 0.0 or self.publish_error_rate > 0.0


class SimulationError(RuntimeError):
    """Raised to represent an injected (fake) failure."""


class FailureInjector:
    """Holds the mutable simulation state for the running process."""

    def __init__(self, settings: SimulationSettings, *, rng: random.Random | None = None) -> None:
        self._state = SimulationState(
            latency_ms=settings.simulate_latency_ms,
            error_rate=settings.simulate_error_rate,
            publish_error_rate=settings.simulate_publish_error_rate,
        )
        self._rng = rng or random.Random()

    @property
    def state(self) -> SimulationState:
        return self._state

    def update(self, new_state: SimulationState) -> SimulationState:
        self._state = new_state
        return self._state

    async def apply_request_latency(self) -> int:
        """Sleep for the configured injected latency. Returns ms slept."""

        if self._state.latency_ms > 0:
            await asyncio.sleep(self._state.latency_ms / 1000)
        return self._state.latency_ms

    def maybe_fail_request(self) -> None:
        if self._state.error_rate > 0.0 and self._rng.random() < self._state.error_rate:
            raise SimulationError("injected order-processing failure")

    def maybe_fail_publish(self) -> None:
        if (
            self._state.publish_error_rate > 0.0
            and self._rng.random() < self._state.publish_error_rate
        ):
            raise SimulationError("injected Kafka publish failure")
