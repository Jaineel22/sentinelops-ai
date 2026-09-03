"""Phase 5D — allow-listed executor abstraction + local simulation.

The executor turns an **already-approved, already-authorized** typed
:class:`~remediation_controller.domain.proposal.RemediationProposal` into a
structured :class:`ExecutionResult`. It:

* receives a typed proposal — **never a command / script / shell string**;
* does **not** check approval or authorization (the service owns that guard);
* does **not** perform state-machine transitions (the service owns that);
* touches **no real infrastructure** — no ``subprocess``, Docker, Kubernetes,
  SSH, cloud SDK, or HTTP-to-infrastructure anywhere.

The only executor in Phase 5D is :class:`LocalSimulationExecutor`, which mutates
a small in-process :class:`SimulationState`. The executor registry is closed and
code-defined — there is no configuration-driven class loading.

Real infrastructure execution is intentionally outside Phase 5D.
"""

from __future__ import annotations

from remediation_controller.executor.base import (
    ExecutionResult,
    Executor,
    ExecutorError,
    new_execution_id,
)
from remediation_controller.executor.registry import EXECUTORS, build_executor
from remediation_controller.executor.simulation import LocalSimulationExecutor, SimulationState

__all__ = [
    "EXECUTORS",
    "ExecutionResult",
    "Executor",
    "ExecutorError",
    "LocalSimulationExecutor",
    "SimulationState",
    "build_executor",
    "new_execution_id",
]
