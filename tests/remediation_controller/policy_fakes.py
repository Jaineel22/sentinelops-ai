"""Test doubles for Phase 5B policy tests.

``FakeHistory`` is an in-test stub of ``RemediationHistoryPort`` — NOT a
persistence implementation. It just returns whatever the test configured.
"""

from __future__ import annotations

from datetime import UTC, datetime

from remediation_controller.domain.enums import RemediationActionType
from remediation_controller.domain.models import ServiceTarget
from remediation_controller.policy import PolicyContext

BASE_TIME = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


class FakeHistory:
    """Configurable stub of ``RemediationHistoryPort``."""

    def __init__(self, *, active: bool = False, last_completed_at: datetime | None = None) -> None:
        self._active = active
        self._last = last_completed_at
        self.calls: list[tuple[str, str, RemediationActionType, str]] = []

    def active_remediation_exists(
        self, *, incident_id: str, action_type: RemediationActionType, target: ServiceTarget
    ) -> bool:
        self.calls.append(("active", incident_id, action_type, str(target)))
        return self._active

    def last_completed_at(
        self, *, incident_id: str, action_type: RemediationActionType, target: ServiceTarget
    ) -> datetime | None:
        self.calls.append(("last", incident_id, action_type, str(target)))
        return self._last


def make_context(
    *,
    now: datetime = BASE_TIME,
    incident_severity: str | None = "HIGH",
    history: FakeHistory | None = None,
) -> PolicyContext:
    return PolicyContext(
        now=now,
        incident_severity=incident_severity,
        history=history or FakeHistory(),
    )
