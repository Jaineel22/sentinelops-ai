"""Structured logging shape (no real OTLP export)."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from sentinelops_common.obs import configure_json_logging


def _last_json_line(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    lines = capsys.readouterr().out.strip().splitlines()
    return json.loads(lines[-1])  # type: ignore[no-any-return]


def test_json_logging_shape(capsys: pytest.CaptureFixture[str]) -> None:
    configure_json_logging(service="incident-correlator", env="test", level="INFO")
    logging.getLogger("x").info("hello", extra={"incident_id": "inc_1", "severity": "HIGH"})

    line = _last_json_line(capsys)
    assert line["service"] == "incident-correlator"
    assert line["environment"] == "test"
    assert line["message"] == "hello"
    assert line["incident_id"] == "inc_1"
    assert line["severity"] == "HIGH"
    assert "trace_id" in line and line["trace_id"] is None  # no active span


def test_exception_is_serialised(capsys: pytest.CaptureFixture[str]) -> None:
    configure_json_logging(service="s", env="test", level="INFO")
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("x").exception("failed")
    line = _last_json_line(capsys)
    assert "ValueError: boom" in line["exception"]
