"""The canonical Kafka event envelope for SentinelOps' business-event backbone.

Every event on every ``*.events`` topic uses this shape. See
docs/architecture/events.md for the full contract (field meanings, versioning
strategy, idempotency rules).

Kept separate from observability telemetry (ADR-008): this envelope carries a
``trace_id`` purely so a consumer can *correlate* an event with the trace that
produced it — metrics/logs/traces themselves travel via OpenTelemetry.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError


class EventDecodeError(ValueError):
    """A Kafka message could not be decoded into a valid :class:`EventEnvelope`."""


class EventEnvelope(BaseModel):
    """Versioned envelope. ``payload`` shape is fixed by ``(event_type, event_version)``."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    event_version: int
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    source: str
    # 32-char lowercase hex of the originating trace, or None.
    trace_id: str | None = None
    payload: dict[str, Any]

    def to_json_bytes(self) -> bytes:
        return self.model_dump_json().encode("utf-8")

    def kafka_headers(self) -> list[tuple[str, bytes]]:
        """Standard headers every producer attaches (in addition to ``traceparent``)."""

        return [
            ("event-type", self.event_type.encode("utf-8")),
            ("event-id", self.event_id.encode("utf-8")),
            ("event-version", str(self.event_version).encode("utf-8")),
        ]


def parse_envelope(raw: bytes | str) -> EventEnvelope:
    """Decode Kafka message bytes into an :class:`EventEnvelope`.

    Raises :class:`EventDecodeError` (never a bare ``JSONDecodeError`` /
    ``ValidationError``) so callers can route malformed messages to a DLQ with a
    single ``except``.
    """

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
        raise EventDecodeError(f"not valid JSON: {exc}") from exc
    try:
        return EventEnvelope.model_validate(data)
    except ValidationError as exc:
        raise EventDecodeError(f"does not match the event envelope: {exc}") from exc
