"""Phase 5D security boundary: no infrastructure primitives, no command field."""

from __future__ import annotations

import ast
from pathlib import Path

import remediation_controller
from remediation_controller.api.schemas import (
    ApprovalRequest,
    CreateRemediationRequest,
    ExecuteRequest,
    RecommendedActionBody,
)
from remediation_controller.db import models as db_models
from remediation_controller.executor.base import ExecutionResult

_BANNED_MODULES = frozenset(
    {
        "subprocess",
        "docker",
        "kubernetes",
        "kubernetes_asyncio",
        "boto3",
        "botocore",
        "paramiko",
        "asyncssh",
        "fabric",
        "requests",
        "httpx",
        "aiohttp",
        "socket",
        "pty",
    }
)
_BANNED_CALLS = frozenset({"eval", "exec", "__import__", "compile"})
_COMMAND_FIELD_NAMES = frozenset(
    {"command", "script", "shell", "cmd", "exec", "run", "kubectl_command", "docker_command"}
)


def _service_py_files() -> list[Path]:
    root = Path(remediation_controller.__file__).parent
    return [p for p in root.rglob("*.py") if "migrations" not in p.parts]


def test_no_service_module_imports_infrastructure() -> None:
    for path in _service_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in _BANNED_MODULES, (
                        f"{path.name}: import {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in _BANNED_MODULES, (
                    f"{path.name}: from {node.module}"
                )
            elif isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name):
                    assert f.id not in _BANNED_CALLS, f"{path.name}: {f.id}(...)"
                if (
                    isinstance(f, ast.Attribute)
                    and isinstance(f.value, ast.Name)
                    and f.value.id == "os"
                ):
                    assert f.attr not in {"system", "popen", "exec", "execv", "execve"}, (
                        f"{path.name}: os.{f.attr}(...)"
                    )


def test_no_shell_true_anywhere() -> None:
    for path in _service_py_files():
        assert "shell=True" not in path.read_text(encoding="utf-8"), path.name


def test_no_command_shaped_field_on_any_request_model() -> None:
    for model in (CreateRemediationRequest, RecommendedActionBody, ApprovalRequest, ExecuteRequest):
        fields = set(model.model_fields)
        assert fields.isdisjoint(_COMMAND_FIELD_NAMES), f"{model.__name__}: {fields}"


def test_execute_request_only_has_dry_run() -> None:
    assert set(ExecuteRequest.model_fields) == {"dry_run"}


def test_execution_result_has_no_command_field() -> None:
    assert set(ExecutionResult.model_fields).isdisjoint(_COMMAND_FIELD_NAMES)


def test_no_db_column_is_command_shaped() -> None:
    for table in db_models.Base.metadata.tables.values():
        cols = {c.name for c in table.columns}
        assert cols.isdisjoint(_COMMAND_FIELD_NAMES), f"{table.name}: {cols}"


def test_executor_receives_a_typed_proposal_not_a_string() -> None:
    import inspect

    from remediation_controller.executor.base import Executor

    sig = inspect.signature(Executor.execute)
    params = list(sig.parameters)
    assert params[1] == "proposal"
    # keyword-only: execution_id, dry_run, now — nothing that could be a command
    assert set(params[2:]) == {"execution_id", "dry_run", "now"}
