"""Phase 5A domain vocabulary — closedness and the terminal/active partition."""

from __future__ import annotations

from remediation_controller.domain import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    ExecutorType,
    RemediationActionType,
    RemediationStatus,
    RiskLevel,
)


def test_action_type_is_a_small_closed_set() -> None:
    assert set(RemediationActionType) == {
        RemediationActionType.RESTART_SERVICE,
        RemediationActionType.SCALE_SERVICE,
        RemediationActionType.ROLL_BACK_DEPLOYMENT,
        RemediationActionType.DISABLE_FEATURE_FLAG,
    }


def test_no_arbitrary_command_action_exists() -> None:
    forbidden = {
        "execute_command",
        "run_shell",
        "shell",
        "command",
        "docker_exec",
        "kubectl_exec",
        "kubectl",
        "arbitrary_script",
        "script",
        "free_form_action",
        "eval",
        "exec",
    }
    names = {a.value.lower() for a in RemediationActionType} | {
        a.name.lower() for a in RemediationActionType
    }
    assert names.isdisjoint(forbidden)


def test_only_local_simulation_executor_exists() -> None:
    assert set(ExecutorType) == {ExecutorType.LOCAL_SIMULATION}


def test_terminal_and_active_partition_the_status_enum() -> None:
    assert ACTIVE_STATUSES.isdisjoint(TERMINAL_STATUSES)
    assert set(RemediationStatus) == ACTIVE_STATUSES | TERMINAL_STATUSES
    assert all(s.is_terminal for s in TERMINAL_STATUSES)
    assert not any(s.is_terminal for s in ACTIVE_STATUSES)


def test_expected_terminal_states() -> None:
    assert {
        RemediationStatus.BLOCKED,
        RemediationStatus.REJECTED,
        RemediationStatus.EXPIRED,
        RemediationStatus.EXECUTION_FAILED,
        RemediationStatus.RECOVERED,
        RemediationStatus.RECOVERY_FAILED,
    } == TERMINAL_STATUSES


def test_risk_level_rank_is_ordered() -> None:
    assert RiskLevel.LOW.rank < RiskLevel.MEDIUM.rank < RiskLevel.HIGH.rank
