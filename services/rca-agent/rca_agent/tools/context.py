"""Per-investigation execution context passed to every tool call.

Owns deterministic evidence-id allocation and the evidence budget. In Sub-phase
4C the engine constructs one ``ToolContext`` per investigation so every evidence
id is unique within that investigation (the RCA validation layer checks report
references against exactly this set).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


@dataclass
class ToolContext:
    max_evidence_items: int = 40
    now_fn: Callable[[], datetime] = _utcnow
    _issued: int = field(default=0, repr=False)

    def next_evidence_id(self) -> str:
        self._issued += 1
        return f"ev_{self._issued:03d}"

    def remaining_evidence(self) -> int:
        return max(self.max_evidence_items - self._issued, 0)

    @property
    def issued(self) -> int:
        return self._issued

    def now(self) -> datetime:
        return self.now_fn()
