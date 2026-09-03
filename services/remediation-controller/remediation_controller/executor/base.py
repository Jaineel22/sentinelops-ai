"""The executor boundary: :class:`ExecutionResult`, the :class:`Executor`
Protocol, and :class:`ExecutorError`.

An :class:`Executor` is **synchronous** and receives a fully validated
:class:`~remediation_controller.domain.proposal.RemediationProposal`. There is no
parameter, field, or method anywhere in this module that accepts a command,
script, shell string, or executor selector.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from remediation_controller.domain.enums import (
    ExecutionStatus,
    ExecutorType,
    RemediationActionType,
)
from remediation_controller.domain.proposal import RemediationProposal

EXECUTION_ID_RE = r"^exec_[0-9a-f]{16}$"


def new_execution_id() -> str:
    return f"exec_{secrets.token_hex(8)}"


class ExecutorError(RuntimeError):
    """The executor could not carry out the (already-authorized) action.

    Raised only for a genuine *execution* failure — never for an authorization
    problem (the service guards that before calling the executor). The service
    turns this into an ``EXECUTION_FAILED`` remediation + a ``FAILED``
    :class:`ExecutionResult`; it never becomes ``EXECUTED``.
    """


class ExecutionResult(BaseModel):
    """The structured outcome of one execution attempt (real or dry-run).

    Immutable. Also the persisted / API shape (mapped to the
    ``remediation_executions`` row).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    execution_id: str = Field(pattern=EXECUTION_ID_RE)
    remediation_id: str
    action_type: RemediationActionType
    target_service: str
    target_environment: str
    executor_type: ExecutorType
    status: ExecutionStatus
    dry_run: bool
    started_at: datetime
    completed_at: datetime | None = None
    simulated_effect: str = Field(default="", max_length=2000)
    metadata: dict[str, str | int | bool] = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=2000)

    @property
    def succeeded(self) -> bool:
        return self.status is ExecutionStatus.SUCCEEDED


class Executor(Protocol):
    """A closed, code-registered execution backend.

    ``execute`` receives a validated proposal and returns a structured result.
    It never checks approval/authorization and never transitions state — those
    are the service's job (Phase 5A ``authorize_execution`` + the 5A state
    machine).
    """

    executor_type: ExecutorType

    def execute(
        self,
        proposal: RemediationProposal,
        *,
        execution_id: str,
        dry_run: bool,
        now: datetime,
    ) -> ExecutionResult: ...
