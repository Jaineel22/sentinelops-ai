"""Structured (JSON) logging with automatic trace correlation.

Every log line is one JSON object with a stable set of fields. ``trace_id`` and
``span_id`` are filled in automatically from the active OpenTelemetry span, so
a log line can always be lined up with its trace. Pass ``event_id`` (and other
safe context) via ``logger.info(msg, extra={"event_id": ...})``.

Never log secrets, credentials, tokens, or full personal data.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from orders_service import SERVICE_NAME
from orders_service.telemetry import current_trace_id

# Reserved LogRecord attributes we must not treat as user-supplied "extra".
_RESERVED = set(
    vars(logging.makeLogRecord({})).keys()
    | {"message", "asctime", "trace_id", "span_id", "taskName"}
)

_CONTEXT_KEYS = ("event_id", "event_type", "topic", "order_currency", "outcome")


class _TraceContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        from opentelemetry import trace

        record.trace_id = current_trace_id()
        span_context = trace.get_current_span().get_span_context()
        record.span_id = (
            trace.format_span_id(span_context.span_id) if span_context.is_valid else None
        )
        return True


class JsonFormatter(logging.Formatter):
    def __init__(self, *, env: str) -> None:
        super().__init__()
        self._env = env

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "service": SERVICE_NAME,
            "environment": self._env,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", None),
            "span_id": getattr(record, "span_id", None),
        }
        for key in _CONTEXT_KEYS:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        # Any other explicitly-passed extras (kept last, still filtered).
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(*, env: str, level: str) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(env=env))
    handler.addFilter(_TraceContextFilter())
    root.addHandler(handler)

    # Uvicorn's own loggers should flow through the same handler.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
