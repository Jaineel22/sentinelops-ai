"""``LocalSimulationExecutor`` — the only Phase 5D executor.

It simulates the operational effect of the four closed catalogue actions on a
small **in-process** :class:`SimulationState`. It never launches a process, opens
a socket, or calls any infrastructure. Dry-run computes the same result without
mutating the simulation state.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from remediation_controller.domain.catalogue import validate_action_parameters
from remediation_controller.domain.enums import (
    ExecutionStatus,
    ExecutorType,
    RemediationActionType,
)
from remediation_controller.domain.proposal import RemediationProposal
from remediation_controller.executor.base import ExecutionResult, ExecutorError

_DEFAULT_FLAGS = {"new_checkout_flow": True, "recommendations_v2": True}

# Phase 5F: a simulated service, once a remediation has been applied, converges
# to healthy this long after the execution completes. The optimistic default is
# "immediately" — a fault injected for a scenario sets a real delay (slow
# recovery, still succeeds if inside the window) or ``chronic_fault`` (never).
_DEFAULT_RECOVERY_DELAY = timedelta(0)


@dataclass
class ServiceSimState:
    running: bool = True
    replicas: int = 3
    deployment_revision: str = "v1"
    restart_count: int = 0
    rollback_count: int = 0
    feature_flags: dict[str, bool] = field(default_factory=lambda: dict(_DEFAULT_FLAGS))

    # --- Phase 5F: simulated post-remediation health trajectory ---
    faulted: bool = False
    recovery_started_at: datetime | None = None
    recovery_delay: timedelta = _DEFAULT_RECOVERY_DELAY
    chronic_fault: bool = False


class SimulationState:
    """Purely in-process. One :class:`ServiceSimState` per service name. There is
    no persistence and no real target behind it."""

    def __init__(self) -> None:
        self._services: dict[str, ServiceSimState] = {}

    def service(self, name: str) -> ServiceSimState:
        return self._services.setdefault(name, ServiceSimState())

    def inject_fault(
        self,
        name: str,
        *,
        recover_after: timedelta | None = None,
        chronic: bool = False,
    ) -> None:
        """Mark a simulated service as degraded (a scenario / test hook only —
        there is no API or runtime path to this).

        ``recover_after`` — once a remediation is applied, the service becomes
        healthy this long afterwards (default: immediately). ``chronic=True`` —
        the service never becomes healthy (the remediation did not fix it).
        """

        s = self.service(name)
        s.faulted = True
        s.running = True
        s.chronic_fault = chronic
        s.recovery_started_at = None
        if recover_after is not None:
            s.recovery_delay = recover_after

    def mark_remediated(self, name: str, now: datetime) -> None:
        """Record that a (real, non-dry-run) remediation was just applied — the
        service now begins its simulated recovery trajectory."""

        self.service(name).recovery_started_at = now

    def snapshot(self, name: str) -> dict[str, str | int | bool]:
        s = self.service(name)
        return {
            "running": s.running,
            "replicas": s.replicas,
            "deployment_revision": s.deployment_revision,
            "restart_count": s.restart_count,
            "rollback_count": s.rollback_count,
            "faulted": s.faulted,
            "chronic_fault": s.chronic_fault,
            **{f"flag:{k}": v for k, v in sorted(s.feature_flags.items())},
        }

    def copy(self) -> SimulationState:
        clone = SimulationState()
        clone._services = copy.deepcopy(self._services)
        return clone


@dataclass(frozen=True)
class _Plan:
    effect: str
    metadata: dict[str, str | int | bool]


class LocalSimulationExecutor:
    """Deterministic, local, no-infrastructure executor."""

    executor_type: ExecutorType = ExecutorType.LOCAL_SIMULATION

    def __init__(self, state: SimulationState | None = None) -> None:
        self._state = state or SimulationState()

    @property
    def state(self) -> SimulationState:
        return self._state

    def execute(
        self,
        proposal: RemediationProposal,
        *,
        execution_id: str,
        dry_run: bool,
        now: datetime,
    ) -> ExecutionResult:
        # Re-validate parameters against the closed catalogue, defensively —
        # nothing here trusts the caller for anything beyond the typed proposal.
        params = validate_action_parameters(proposal.action_type, proposal.parameters)
        service = proposal.target.service_name

        # `target` is used only against a *simulated* copy for dry-run so the
        # real simulation state is never touched.
        working = self._state if not dry_run else self._state.copy()
        plan = self._apply(proposal.action_type, service, params, working)
        if not dry_run:
            # A real remediation was applied — the simulated service now begins
            # its recovery trajectory (observed later by the Phase 5F verifier).
            working.mark_remediated(service, now)

        prefix = "[DRY RUN] would " if dry_run else ""
        return ExecutionResult(
            execution_id=execution_id,
            remediation_id=proposal.remediation_id,
            action_type=proposal.action_type,
            target_service=service,
            target_environment=proposal.target.environment,
            executor_type=self.executor_type,
            status=ExecutionStatus.SUCCEEDED,
            dry_run=dry_run,
            started_at=now,
            completed_at=now,
            simulated_effect=(prefix + plan.effect)[:2000],
            metadata={
                **plan.metadata,
                "dry_run": dry_run,
                "resulting_state": str(working.snapshot(service)),
            },
        )

    def _apply(
        self,
        action: RemediationActionType,
        service: str,
        params: dict[str, str | int | bool],
        state: SimulationState,
    ) -> _Plan:
        s = state.service(service)
        if action is RemediationActionType.RESTART_SERVICE:
            s.restart_count += 1
            s.running = True
            return _Plan(
                f"restart {service}: bounce all instances (restart #{s.restart_count})",
                {"restart_count": s.restart_count, "running": s.running},
            )
        if action is RemediationActionType.SCALE_SERVICE:
            replicas = params["replicas"]
            assert isinstance(replicas, int)
            prev_replicas, s.replicas = s.replicas, replicas
            return _Plan(
                f"scale {service} from {prev_replicas} to {replicas} replicas",
                {"replicas": replicas, "previous_replicas": prev_replicas},
            )
        if action is RemediationActionType.ROLL_BACK_DEPLOYMENT:
            to_revision = params.get("to_revision")
            prev_revision = s.deployment_revision
            target_rev = str(to_revision) if to_revision else f"{prev_revision}-prev"
            s.deployment_revision = target_rev
            s.rollback_count += 1
            return _Plan(
                f"roll {service} back from {prev_revision} to {target_rev}",
                {"revision": target_rev, "previous_revision": prev_revision},
            )
        if action is RemediationActionType.DISABLE_FEATURE_FLAG:
            flag_key = str(params["flag_key"])
            previous_value = s.feature_flags.get(flag_key, True)
            s.feature_flags[flag_key] = False
            return _Plan(
                f"disable feature flag {flag_key!r} on {service}",
                {"flag_key": flag_key, "previously_enabled": previous_value, "enabled": False},
            )
        # RemediationActionType is a closed enum and the catalogue is total, so
        # this is unreachable — but fail loudly rather than silently no-op.
        raise ExecutorError(  # pragma: no cover
            f"LocalSimulationExecutor cannot simulate action {action!r}"
        )


# Every catalogue action the executor claims to handle — asserted at import so an
# incomplete edit fails fast (mirrors the catalogue's own totality assertion).
SIMULATED_ACTIONS: frozenset[RemediationActionType] = frozenset(
    {
        RemediationActionType.RESTART_SERVICE,
        RemediationActionType.SCALE_SERVICE,
        RemediationActionType.ROLL_BACK_DEPLOYMENT,
        RemediationActionType.DISABLE_FEATURE_FLAG,
    }
)
assert set(RemediationActionType) == SIMULATED_ACTIONS, (
    "LocalSimulationExecutor must handle every catalogue action"
)
