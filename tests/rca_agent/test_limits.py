"""Investigation resource limits (agent-loop safety)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from rca_agent.limits import (
    LimitExceeded,
    ResourceLimits,
    ResourceUsage,
    check_limits,
)


def test_defaults_are_sane() -> None:
    limits = ResourceLimits()
    assert limits.max_tool_calls == 12
    assert limits.max_steps == 25
    assert limits.max_evidence_items == 40
    assert limits.timeout_seconds == 120.0
    assert limits.max_hypotheses == 5


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_tool_calls": 0},
        {"max_tool_calls": 1000},
        {"max_steps": 0},
        {"max_evidence_items": 500},
        {"timeout_seconds": 0},
        {"timeout_seconds": 10_000},
        {"max_hypotheses": 100},
    ],
)
def test_out_of_range_limits_are_rejected(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        ResourceLimits(**kwargs)  # type: ignore[arg-type]


def test_limits_are_frozen() -> None:
    limits = ResourceLimits()
    with pytest.raises(ValidationError):
        limits.max_tool_calls = 99


def test_check_limits_passes_within_budget() -> None:
    usage = ResourceUsage(tool_calls=3, steps=5, evidence_items=4)
    check_limits(usage, ResourceLimits())


@pytest.mark.parametrize(
    ("usage", "kind"),
    [
        (ResourceUsage(tool_calls=13), "tool_calls"),
        (ResourceUsage(steps=26), "steps"),
        (ResourceUsage(evidence_items=41), "evidence_items"),
    ],
)
def test_check_limits_raises_on_count_overflow(usage: ResourceUsage, kind: str) -> None:
    with pytest.raises(LimitExceeded) as exc:
        check_limits(usage, ResourceLimits())
    assert exc.value.kind == kind


def test_check_limits_raises_on_timeout() -> None:
    started = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    usage = ResourceUsage(started_at=started)
    now = started + timedelta(seconds=121)
    with pytest.raises(LimitExceeded) as exc:
        check_limits(usage, ResourceLimits(), now=now)
    assert exc.value.kind == "time"


def test_elapsed_seconds() -> None:
    started = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    usage = ResourceUsage(started_at=started)
    assert usage.elapsed_seconds(now=started + timedelta(seconds=5)) == pytest.approx(5.0)
