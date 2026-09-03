"""LocalSimulationExecutor + the closed executor registry (Phase 5D)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from remediation_controller.domain import (
    RemediationActionType,
    RemediationProposal,
    RemediationTrigger,
    RiskLevel,
    ServiceTarget,
)
from remediation_controller.domain.enums import ExecutionStatus, ExecutorType
from remediation_controller.executor import (
    EXECUTORS,
    LocalSimulationExecutor,
    build_executor,
    new_execution_id,
)
from remediation_controller.executor.registry import UnknownExecutorError

_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


def _proposal(
    action: RemediationActionType, params: dict[str, str | int | bool] | None = None
) -> RemediationProposal:
    return RemediationProposal(
        remediation_id="rem_00112233aabbccdd",
        incident_id="inc_00112233aabbccdd",
        trigger=RemediationTrigger.RCA_RECOMMENDATION,
        proposed_by="x",
        action_type=action,
        target=ServiceTarget(service_name="orders-service", environment="development"),
        parameters=params or {},
        risk_level=RiskLevel.MEDIUM,
        created_at=_NOW,
        expires_at=_NOW + timedelta(hours=1),
    )


# --- registry -----------------------------------------------------
def test_registry_is_closed_and_total() -> None:
    assert set(EXECUTORS) == set(ExecutorType) == {ExecutorType.LOCAL_SIMULATION}


def test_registry_is_read_only() -> None:
    with pytest.raises(TypeError):
        EXECUTORS[ExecutorType.LOCAL_SIMULATION] = None  # type: ignore[index]


def test_build_executor_returns_the_local_simulation_executor() -> None:
    ex = build_executor(ExecutorType.LOCAL_SIMULATION)
    assert isinstance(ex, LocalSimulationExecutor)
    assert ex.executor_type is ExecutorType.LOCAL_SIMULATION


def test_no_dynamic_class_loading_in_the_registry() -> None:
    from pathlib import Path

    from remediation_controller.executor import registry

    src = Path(registry.__file__).read_text(encoding="utf-8")
    for banned in ("import_module", "__import__", "importlib", "getattr(", "eval(", "exec("):
        assert banned not in src


# --- every catalogue action -------------------------------------
@pytest.mark.parametrize(
    ("action", "params", "effect_contains", "state_key", "expected_change"),
    [
        (RemediationActionType.RESTART_SERVICE, {}, "restart", "restart_count", 1),
        (RemediationActionType.SCALE_SERVICE, {"replicas": 7}, "scale", "replicas", 7),
        (
            RemediationActionType.ROLL_BACK_DEPLOYMENT,
            {"to_revision": "v0.9"},
            "roll",
            "deployment_revision",
            "v0.9",
        ),
        (
            RemediationActionType.DISABLE_FEATURE_FLAG,
            {"flag_key": "new_checkout_flow"},
            "disable feature flag",
            "flag:new_checkout_flow",
            False,
        ),
    ],
)
def test_real_execution_simulates_each_action(
    action: RemediationActionType,
    params: dict[str, str | int | bool],
    effect_contains: str,
    state_key: str,
    expected_change: object,
) -> None:
    ex = LocalSimulationExecutor()
    p = _proposal(action, params)
    result = ex.execute(p, execution_id=new_execution_id(), dry_run=False, now=_NOW)

    assert result.status is ExecutionStatus.SUCCEEDED
    assert result.dry_run is False
    assert result.remediation_id == p.remediation_id
    assert result.action_type is action
    assert result.target_service == "orders-service"
    assert result.target_environment == "development"
    assert result.executor_type is ExecutorType.LOCAL_SIMULATION
    assert effect_contains in result.simulated_effect
    assert ex.state.snapshot("orders-service")[state_key] == expected_change


def test_result_is_immutable() -> None:
    ex = LocalSimulationExecutor()
    r = ex.execute(
        _proposal(RemediationActionType.RESTART_SERVICE),
        execution_id=new_execution_id(),
        dry_run=False,
        now=_NOW,
    )
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        r.status = ExecutionStatus.FAILED


# --- dry run -----------------------------------------------------
@pytest.mark.parametrize(
    ("action", "params"),
    [
        (RemediationActionType.RESTART_SERVICE, {}),
        (RemediationActionType.SCALE_SERVICE, {"replicas": 9}),
        (RemediationActionType.ROLL_BACK_DEPLOYMENT, {}),
        (RemediationActionType.DISABLE_FEATURE_FLAG, {"flag_key": "recommendations_v2"}),
    ],
)
def test_dry_run_does_not_mutate_state(
    action: RemediationActionType, params: dict[str, str | int | bool]
) -> None:
    ex = LocalSimulationExecutor()
    before = ex.state.snapshot("orders-service")
    result = ex.execute(
        _proposal(action, params), execution_id=new_execution_id(), dry_run=True, now=_NOW
    )
    assert result.status is ExecutionStatus.SUCCEEDED
    assert result.dry_run is True
    assert result.simulated_effect.startswith("[DRY RUN]")
    assert ex.state.snapshot("orders-service") == before  # unchanged


def test_dry_run_then_real_are_consistent() -> None:
    ex = LocalSimulationExecutor()
    dry = ex.execute(
        _proposal(RemediationActionType.SCALE_SERVICE, {"replicas": 8}),
        execution_id=new_execution_id(),
        dry_run=True,
        now=_NOW,
    )
    real = ex.execute(
        _proposal(RemediationActionType.SCALE_SERVICE, {"replicas": 8}),
        execution_id=new_execution_id(),
        dry_run=False,
        now=_NOW,
    )
    assert "8" in dry.simulated_effect and "8" in real.simulated_effect
    assert ex.state.snapshot("orders-service")["replicas"] == 8


# --- no infrastructure primitives ------------------------------
_BANNED_MODULES = frozenset(
    {
        "subprocess",
        "docker",
        "kubernetes",
        "kubernetes_asyncio",
        "boto3",
        "botocore",
        "paramiko",
        "fabric",
        "requests",
        "httpx",
        "aiohttp",
        "socket",
        "pty",
        "asyncssh",
    }
)


def test_executor_package_imports_no_infrastructure_module() -> None:
    import ast
    from pathlib import Path

    import remediation_controller.executor as pkg

    for path in Path(pkg.__file__).parent.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in _BANNED_MODULES, f"{path.name}: import {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                assert root not in _BANNED_MODULES, f"{path.name}: from {node.module}"
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    assert not (
                        func.value.id == "os" and func.attr in {"system", "popen", "exec", "execv"}
                    ), f"{path.name}: os.{func.attr}(...)"
                if isinstance(func, ast.Name):
                    assert func.id not in {"eval", "exec", "__import__", "compile"}, (
                        f"{path.name}: {func.id}(...)"
                    )


def test_unknown_executor_type_fails_closed() -> None:
    with pytest.raises((UnknownExecutorError, KeyError, ValueError)):
        build_executor("SSH_EXECUTOR")  # type: ignore[arg-type]
