"""HTTP investigation API (Sub-phase 4E).

Synchronous ``TestClient`` with ``run_investigations_in_background=False`` so each
POST returns a finished investigation — deterministic. The background path is
covered by ``test_api_runner.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from rca_agent.app import create_app
from rca_agent.config import Settings
from rca_agent.repository import InMemoryInvestigationRepository
from tests.rca_agent.incident_api_fakes import (
    INCIDENT_ID,
    make_anomaly_windows,
    make_incident,
    scenario_handler,
)

_ABN = ["error_rate", "latency_p95_ms"]
_HANDLER = scenario_handler(
    incident=make_incident(abnormal=_ABN),
    anomalies=make_anomaly_windows(count=4, abnormal=_ABN, score=0.93),
)


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(
        Settings(),
        repository=InMemoryInvestigationRepository(),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_HANDLER)),
        run_consumer=False,
        run_investigations_in_background=False,
    )
    with TestClient(app) as c:
        yield c


# --- system ------------------------------------------------------------
def test_health_is_ok(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_ready_is_ok_without_a_db_or_consumer(client: TestClient) -> None:
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_ready_is_503_when_the_consumer_is_unhealthy(client: TestClient) -> None:
    client.app.state.consumer = SimpleNamespace(healthy=False)  # type: ignore[attr-defined]
    assert client.get("/ready").status_code == 503


def test_metrics_endpoint_exposes_rca_instruments(client: TestClient) -> None:
    client.post("/investigations", json={"incident_id": INCIDENT_ID})
    body = client.get("/metrics").text
    assert "rca_investigations_started" in body or "rca.investigations.started" in body


# --- POST /investigations -------------------------------------------
def test_post_creates_an_investigation_and_returns_202(client: TestClient) -> None:
    r = client.post("/investigations", json={"incident_id": INCIDENT_ID})
    assert r.status_code == 202
    body = r.json()
    assert body["investigation"]["incident_id"] == INCIDENT_ID
    assert body["investigation"]["trigger"] == "MANUAL"
    assert body["investigation"]["status"] in {
        "COMPLETED",
        "INSUFFICIENT_EVIDENCE",
        "FAILED",
        "TIMED_OUT",
    }
    assert body["steps"], "the operational trace must be exposed"
    # a completed investigation carries a validated, human-approval-gated report
    if body["report"] is not None:
        assert body["report"]["recommended_action"]["requires_human_approval"] is True


def test_post_is_idempotent_per_incident(client: TestClient) -> None:
    first = client.post("/investigations", json={"incident_id": INCIDENT_ID}).json()
    again = client.post("/investigations", json={"incident_id": INCIDENT_ID})
    assert again.status_code == 200
    assert again.json()["investigation"]["id"] == first["investigation"]["id"]


def test_post_rejects_a_malformed_incident_id(client: TestClient) -> None:
    assert client.post("/investigations", json={"incident_id": "not-an-id"}).status_code == 422
    assert client.post("/investigations", json={}).status_code == 422


def test_post_rejects_unknown_fields(client: TestClient) -> None:
    r = client.post("/investigations", json={"incident_id": INCIDENT_ID, "run_command": "rm -rf /"})
    assert r.status_code == 422


# --- GET endpoints -------------------------------------------------
def test_get_investigation_by_id(client: TestClient) -> None:
    created = client.post("/investigations", json={"incident_id": INCIDENT_ID}).json()
    inv_id = created["investigation"]["id"]
    r = client.get(f"/investigations/{inv_id}")
    assert r.status_code == 200
    assert r.json()["investigation"]["id"] == inv_id
    assert r.json()["steps"]


def test_get_unknown_investigation_is_404(client: TestClient) -> None:
    assert client.get("/investigations/rca_deadbeef").status_code == 404


def test_get_investigation_steps_endpoint(client: TestClient) -> None:
    created = client.post("/investigations", json={"incident_id": INCIDENT_ID}).json()
    inv_id = created["investigation"]["id"]
    r = client.get(f"/investigations/{inv_id}/steps")
    assert r.status_code == 200
    steps = r.json()
    assert isinstance(steps, list) and steps
    assert [s["seq"] for s in steps] == sorted(s["seq"] for s in steps)
    for step in steps:
        assert set(step) >= {"seq", "kind", "phase", "description", "at"}
        assert "chain_of_thought" not in step and "raw_reasoning" not in step
    assert client.get("/investigations/rca_deadbeef/steps").status_code == 404


def test_get_latest_investigation_for_an_incident(client: TestClient) -> None:
    client.post("/investigations", json={"incident_id": INCIDENT_ID})
    r = client.get(f"/incidents/{INCIDENT_ID}/investigation")
    assert r.status_code == 200
    assert r.json()["investigation"]["incident_id"] == INCIDENT_ID


def test_get_investigation_for_an_uninvestigated_incident_is_404(client: TestClient) -> None:
    assert client.get("/incidents/inc_00000000/investigation").status_code == 404


def test_response_carries_the_trace_not_hidden_reasoning(client: TestClient) -> None:
    body = client.post("/investigations", json={"incident_id": INCIDENT_ID}).json()
    blob = str(body)
    assert "chain_of_thought" not in blob and "raw_reasoning" not in blob
    # steps describe actions/results
    for step in body["steps"]:
        assert step["description"]
        assert set(step) >= {"seq", "kind", "phase", "description"}
