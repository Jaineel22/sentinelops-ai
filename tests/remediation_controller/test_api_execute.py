"""POST /remediations/{id}/execute — endpoint + security payloads (Phase 5D)."""

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


def _approved(
    client: TestClient, *, action: str = "RESTART_SERVICE", role: str = "INCIDENT_RESPONDER"
) -> str:
    body: dict[str, Any] = {
        "incident_id": _INCIDENT,
        "incident_severity": "HIGH",
        "recommended_action": {"action_type": action, "target_service": "orders-service"},
    }
    rid = str(client.post("/remediations", json=body).json()["remediation_id"])
    r = client.post(
        f"/remediations/{rid}/approve",
        json={"approver_identity": "alice@x", "approver_role": role},
    )
    assert r.status_code == 200, r.text
    return rid


# --- happy path -------------------------------------------------
def test_execute_transitions_to_executed(client: TestClient) -> None:
    rid = _approved(client)
    r = client.post(f"/remediations/{rid}/execute", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "EXECUTED"
    assert body["status"] not in {"EXECUTING", "PROPOSED", "PENDING_APPROVAL"}
    assert body["execution"]["status"] == "SUCCEEDED"
    assert body["execution"]["dry_run"] is False
    assert body["execution"]["executor_type"] == "LOCAL_SIMULATION"
    assert "restart" in body["execution"]["simulated_effect"]
    assert "command" not in body and "script" not in body


def test_execute_is_reflected_in_get(client: TestClient) -> None:
    rid = _approved(client)
    client.post(f"/remediations/{rid}/execute", json={})
    got = client.get(f"/remediations/{rid}").json()
    assert got["status"] == "EXECUTED"
    assert got["execution"]["status"] == "SUCCEEDED"


# --- dry run ---------------------------------------------------
def test_dry_run_returns_preview_without_changing_status(client: TestClient) -> None:
    rid = _approved(client)
    r = client.post(f"/remediations/{rid}/execute", json={"dry_run": True})
    assert r.status_code == 200
    body = r.json()
    assert body["execution"]["dry_run"] is True
    assert body["execution"]["simulated_effect"].startswith("[DRY RUN]")
    # remediation is still APPROVED and can be really executed afterwards
    assert client.get(f"/remediations/{rid}").json()["status"] == "APPROVED"
    assert client.get(f"/remediations/{rid}").json()["execution"] is None
    assert client.post(f"/remediations/{rid}/execute", json={}).json()["status"] == "EXECUTED"


def test_dry_run_of_unapproved_is_409(client: TestClient) -> None:
    body: dict[str, Any] = {
        "incident_id": _INCIDENT,
        "incident_severity": "HIGH",
        "recommended_action": {
            "action_type": "RESTART_SERVICE",
            "target_service": "orders-service",
        },
    }
    rid = client.post("/remediations", json=body).json()["remediation_id"]
    assert client.post(f"/remediations/{rid}/execute", json={"dry_run": True}).status_code == 409


# --- guards --------------------------------------------------
def test_execute_unapproved_is_409(client: TestClient) -> None:
    body: dict[str, Any] = {
        "incident_id": _INCIDENT,
        "incident_severity": "HIGH",
        "recommended_action": {
            "action_type": "RESTART_SERVICE",
            "target_service": "orders-service",
        },
    }
    rid = client.post("/remediations", json=body).json()["remediation_id"]
    assert client.post(f"/remediations/{rid}/execute", json={}).status_code == 409


def test_execute_rejected_is_409(client: TestClient) -> None:
    body: dict[str, Any] = {
        "incident_id": _INCIDENT,
        "incident_severity": "HIGH",
        "recommended_action": {
            "action_type": "RESTART_SERVICE",
            "target_service": "orders-service",
        },
    }
    rid = client.post("/remediations", json=body).json()["remediation_id"]
    client.post(
        f"/remediations/{rid}/reject", json={"approver_identity": "x", "approver_role": "OPERATOR"}
    )
    assert client.post(f"/remediations/{rid}/execute", json={}).status_code == 409


def test_execute_blocked_is_409(client: TestClient) -> None:
    body: dict[str, Any] = {
        "incident_id": _INCIDENT,
        "incident_severity": "HIGH",
        "target_environment": "staging",  # policy denies -> BLOCKED
        "recommended_action": {
            "action_type": "RESTART_SERVICE",
            "target_service": "orders-service",
        },
    }
    rid = client.post("/remediations", json=body).json()["remediation_id"]
    assert client.post(f"/remediations/{rid}/execute", json={}).status_code == 409


def test_execute_unknown_remediation_is_404(client: TestClient) -> None:
    assert client.post("/remediations/rem_ffffffffffffffff/execute", json={}).status_code == 404


def test_double_execution_is_409(client: TestClient) -> None:
    rid = _approved(client)
    assert client.post(f"/remediations/{rid}/execute", json={}).status_code == 200
    assert client.post(f"/remediations/{rid}/execute", json={}).status_code == 409


# --- security payloads -------------------------------------
@pytest.mark.parametrize(
    "poison",
    [
        {"command": "kubectl delete deployment orders-service"},
        {"script": "rm -rf /"},
        {"shell": "/bin/sh"},
        {"executor": "SSHExecutor"},
        {"executor_class": "os.system"},
        {"docker_command": "docker rm -f all"},
        {"aws_command": "aws ecs update-service"},
        {"kubectl_command": "kubectl scale"},
        {"replicas": 999},
        {"target": "prod-cluster"},
    ],
)
def test_execute_rejects_any_extra_field(client: TestClient, poison: dict[str, Any]) -> None:
    rid = _approved(client)
    r = client.post(f"/remediations/{rid}/execute", json={"dry_run": False, **poison})
    assert r.status_code == 422


def test_execute_body_only_accepts_dry_run(client: TestClient) -> None:
    rid = _approved(client)
    # the ONLY accepted field
    assert client.post(f"/remediations/{rid}/execute", json={"dry_run": True}).status_code == 200


def test_no_execute_response_contains_a_command_field(client: TestClient) -> None:
    rid = _approved(client)
    blob = client.post(f"/remediations/{rid}/execute", json={}).text
    assert '"command"' not in blob and '"script"' not in blob and '"shell"' not in blob


def test_metrics_expose_execution_instruments(client: TestClient) -> None:
    rid = _approved(client)
    client.post(f"/remediations/{rid}/execute", json={"dry_run": True})
    client.post(f"/remediations/{rid}/execute", json={})
    body = client.get("/metrics").text
    assert "remediation_executions" in body or "remediation.executions" in body


def test_unauthorized_then_executed_flow_is_blocked_at_approval(client: TestClient) -> None:
    # OPERATOR cannot approve a HIGH-risk ROLL_BACK_DEPLOYMENT, so it never
    # becomes APPROVED and therefore can never be executed.
    body: dict[str, Any] = {
        "incident_id": _INCIDENT,
        "incident_severity": "HIGH",
        "recommended_action": {
            "action_type": "ROLL_BACK_DEPLOYMENT",
            "target_service": "orders-service",
        },
    }
    rid = client.post("/remediations", json=body).json()["remediation_id"]
    assert (
        client.post(
            f"/remediations/{rid}/approve",
            json={"approver_identity": "op", "approver_role": "OPERATOR"},
        ).status_code
        == 403
    )
    assert client.post(f"/remediations/{rid}/execute", json={}).status_code == 409
