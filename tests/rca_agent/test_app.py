"""rca-agent application factory / lifecycle (Sub-phase 4E)."""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from rca_agent.app import create_app
from rca_agent.config import Settings
from rca_agent.repository import InMemoryInvestigationRepository
from tests.rca_agent.incident_api_fakes import routing_handler


def _app(**kw: object) -> object:
    return create_app(
        Settings(),
        repository=InMemoryInvestigationRepository(),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(routing_handler)),
        run_consumer=False,
        run_investigations_in_background=False,
        **kw,  # type: ignore[arg-type]
    )


def test_app_boots_and_exposes_the_documented_routes() -> None:
    with TestClient(_app()) as c:  # type: ignore[arg-type]
        paths = set(c.app.openapi()["paths"])  # type: ignore[attr-defined]
    assert {
        "/health",
        "/ready",
        "/investigations",
        "/investigations/{investigation_id}",
        "/investigations/{investigation_id}/steps",
        "/incidents/{incident_id}/investigation",
    } <= paths


def test_module_level_app_is_importable_in_mock_mode() -> None:
    # `python -m rca_agent` imports rca_agent.app:app at module load; mock mode
    # must not require a key, a DB connection, or Kafka.
    from rca_agent.app import app

    assert app.title == "rca-agent"


def test_default_settings_are_ci_safe() -> None:
    s = Settings()
    assert s.rca.mode == "mock"
    assert s.llm.provider == "mock"
    assert s.kafka.incident_topic == "incident.events"
    assert s.kafka.incident_dlq_topic == "incident.events.dlq"
    assert s.kafka.consumer_group == "rca-agent"


def test_ready_reports_not_ready_when_a_consumer_is_wired_but_down() -> None:
    from types import SimpleNamespace

    with TestClient(_app()) as c:  # type: ignore[arg-type]
        c.app.state.consumer = SimpleNamespace(healthy=False)  # type: ignore[attr-defined]
        r = c.get("/ready")
        assert r.status_code == 503
        assert r.json()["consumer"] == "down"
