"""The closed, code-defined executor registry.

There is exactly one executor in Phase 5D. The registry is a plain dict keyed by
:class:`~remediation_controller.domain.enums.ExecutorType` — **no** environment
variable, import path, or API input can add or select an executor
implementation. ``build_executor`` fails closed for an unknown type.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType

from remediation_controller.domain.enums import ExecutorType
from remediation_controller.executor.base import Executor
from remediation_controller.executor.simulation import LocalSimulationExecutor

_FACTORIES: dict[ExecutorType, Callable[[], Executor]] = {
    ExecutorType.LOCAL_SIMULATION: LocalSimulationExecutor,
}

EXECUTORS: Mapping[ExecutorType, Callable[[], Executor]] = MappingProxyType(_FACTORIES)

# The registry must cover every enumerated executor type (and no more).
assert set(EXECUTORS) == set(ExecutorType), "executor registry must be total and closed"


class UnknownExecutorError(ValueError):
    """The requested executor type has no registered implementation."""


def build_executor(executor_type: ExecutorType) -> Executor:
    factory = _FACTORIES.get(executor_type)
    if factory is None:  # pragma: no cover - ExecutorType is a closed enum
        raise UnknownExecutorError(f"no executor registered for {executor_type!r}")
    return factory()
