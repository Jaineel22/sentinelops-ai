"""GET /remediations/{id}/audit — read-only append-only audit endpoint (Phase 5E)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from remediation_controller.app import create_app
from remediation_controller.config import Settings
from remediation_controller.repository import InMemoryRemediationRepository

_INCIDENT = "inc_00112233aabbccdd"


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(Settings(), repository=InMemoryRemediationRepository(), run_publisher=False)
    with TestClient(app) as c:
        yield c


def _propose(client: TestClient, **over: Any) -> str:
    body: dict[str, Any] = {
        "incident_id": _INCIDENT,
        "incident_severity": "HIGH",
        "recommended_action": {
            "action_type": "RESTART_SERVICE",
            "target_service": "orders-service",
        },
    }
    body.update(over)
    return str(client.post("/remediations", json=body).json()["remediation_id"])


def _approve_execute(client: TestClient, rid: str) -> None:
    assert (
        client.post(
            f"/remediations/{rid}/approve",
            json={"approver_identity": "alice@x", "approver_role": "ADMINISTRATOR"},
        ).status_code
        == 200
    )
    assert client.post(f"/remediations/{rid}/execute", json={}).status_code == 200


def test_audit_endpoint_returns_events_chronologically(client: TestClient) -> None:
    rid = _propose(client)
    _approve_execute(client, rid)
    r = client.get(f"/remediations/{rid}/audit")
    assert r.status_code == 200
    body = r.json()
    assert body["remediation_id"] == rid
    assert body["count"] == 6
    assert [e["event_type"] for e in body["events"]] == [
        "PROPOSAL_CREATED",
        "POLICY_EVALUATED",
        "APPROVED",
        "EXECUTION_REQUESTED",
        "EXECUTION_STARTED",
        "EXECUTION_SUCCEEDED",
    ]
    ts = [e["occurred_at"] for e in body["events"]]
    assert ts == sorted(ts)


def test_audit_endpoint_404_for_unknown_remediation(client: TestClient) -> None:
    assert client.get("/remediations/rem_ffffffffffffffff/audit").status_code == 404


def test_audit_endpoint_paginates(client: TestClient) -> None:
    rid = _propose(client)
    _approve_execute(client, rid)
    first = client.get(f"/remediations/{rid}/audit", params={"limit": 3, "offset": 0}).json()
    rest = client.get(f"/remediations/{rid}/audit", params={"limit": 3, "offset": 3}).json()
    assert first["count"] == 3 and rest["count"] == 3
    assert first["events"][0]["event_type"] == "PROPOSAL_CREATED"
    assert rest["events"][-1]["event_type"] == "EXECUTION_SUCCEEDED"


def test_audit_endpoint_rejects_bad_pagination(client: TestClient) -> None:
    rid = _propose(client)
    assert client.get(f"/remediations/{rid}/audit", params={"limit": 0}).status_code == 422
    assert client.get(f"/remediations/{rid}/audit", params={"offset": -1}).status_code == 422


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_audit_trail_has_no_write_route(client: TestClient, method: str) -> None:
    rid = _propose(client)
    resp = client.request(method.upper(), f"/remediations/{rid}/audit")
    assert resp.status_code in (404, 405)  # no such route / method not allowed


def test_audit_endpoint_echoes_correlation_id(client: TestClient) -> None:
    body: dict[str, Any] = {
        "incident_id": _INCIDENT,
        "incident_severity": "HIGH",
        "recommended_action": {
            "action_type": "RESTART_SERVICE",
            "target_service": "orders-service",
        },
    }
    rid = client.post("/remediations", json=body, headers={"x-request-id": "req-abc-123"}).json()[
        "remediation_id"
    ]
    events = client.get(f"/remediations/{rid}/audit").json()["events"]
    assert all(e["correlation_id"] == "req-abc-123" for e in events)


def test_blocked_remediation_still_has_an_audit_trail(client: TestClient) -> None:
    rid = _propose(client, target_environment="staging")  # policy denies -> BLOCKED
    body = client.get(f"/remediations/{rid}/audit").json()
    assert [e["event_type"] for e in body["events"]] == [
        "PROPOSAL_CREATED",
        "POLICY_EVALUATED",
        "REMEDIATION_BLOCKED",
    ]
    blocked = body["events"][-1]
    assert blocked["new_state"] == "BLOCKED"
    assert blocked["policy_outcome"] == "DENY"
    assert blocked["policy_reason_codes"]


def test_audit_response_carries_no_command_shaped_field(client: TestClient) -> None:
    rid = _propose(client)
    _approve_execute(client, rid)
    blob = client.get(f"/remediations/{rid}/audit").text
    for banned in ('"command"', '"script"', '"shell"', '"kubectl_command"'):
        assert banned not in blob


def test_metrics_expose_audit_instrument(client: TestClient) -> None:
    rid = _propose(client)
    _approve_execute(client, rid)
    body = client.get("/metrics").text
    assert "remediation_audit_events_written" in body or "remediation.audit.events_written" in body
