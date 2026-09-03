"""POST /remediations/{id}/verify-recovery — endpoint + payloads (Phase 5F)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from remediation_controller.app import create_app
from remediation_controller.config import Settings
from remediation_controller.executor.simulation import LocalSimulationExecutor, SimulationState
from remediation_controller.recovery.config import RecoveryVerificationConfig
from remediation_controller.repository import InMemoryRemediationRepository

_INCIDENT = "inc_00112233aabbccdd"
_FAST = RecoveryVerificationConfig(timeout_seconds=4, poll_interval_seconds=1.0)


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(
        Settings(),
        repository=InMemoryRemediationRepository(),
        verify_config=_FAST,
        run_publisher=False,
    )
    with TestClient(app) as c:
        yield c


@pytest.fixture
def chronic_client() -> Iterator[TestClient]:
    state = SimulationState()
    state.inject_fault("orders-service", chronic=True)
    app = create_app(
        Settings(),
        repository=InMemoryRemediationRepository(),
        executor=LocalSimulationExecutor(state),
        verify_config=_FAST,
        run_publisher=False,
    )
    with TestClient(app) as c:
        yield c


def _executed(client: TestClient) -> str:
    body: dict[str, Any] = {
        "incident_id": _INCIDENT,
        "incident_severity": "HIGH",
        "recommended_action": {
            "action_type": "RESTART_SERVICE",
            "target_service": "orders-service",
        },
    }
    rid = str(client.post("/remediations", json=body).json()["remediation_id"])
    assert (
        client.post(
            f"/remediations/{rid}/approve",
            json={"approver_identity": "alice@x", "approver_role": "ADMINISTRATOR"},
        ).status_code
        == 200
    )
    assert client.post(f"/remediations/{rid}/execute", json={}).status_code == 200
    return rid


# --- happy path -----------------------------------------------
def test_verify_recovery_returns_recovered_with_structured_evidence(client: TestClient) -> None:
    rid = _executed(client)
    r = client.post(f"/remediations/{rid}/verify-recovery", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "RECOVERED"
    v = body["verification"]
    assert v["status"] == "RECOVERED"
    assert v["verifier_type"] == "DETERMINISTIC_LOCAL"
    assert v["attempts"] >= 1
    assert isinstance(v["checks"], list) and v["checks"]
    assert all(c["passed"] for c in v["checks"])
    assert {c["name"] for c in v["checks"]} >= {"service_running", "error_rate", "readiness"}
    assert v["failure_reason"] is None
    assert "command" not in body and "script" not in body


def test_verify_recovery_is_reflected_in_get(client: TestClient) -> None:
    rid = _executed(client)
    client.post(f"/remediations/{rid}/verify-recovery", json={})
    got = client.get(f"/remediations/{rid}").json()
    assert got["status"] == "RECOVERED"
    assert got["verification"]["status"] == "RECOVERED"


def test_verify_recovery_failure_returns_recovery_failed(chronic_client: TestClient) -> None:
    client = chronic_client
    rid = _executed(client)
    r = client.post(f"/remediations/{rid}/verify-recovery", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "RECOVERY_FAILED"
    assert body["verification"]["status"] == "RECOVERY_FAILED"
    assert body["verification"]["failure_reason"]
    assert not all(c["passed"] for c in body["verification"]["checks"])


# --- guards ---------------------------------------------------
def test_verify_unknown_remediation_is_404(client: TestClient) -> None:
    assert (
        client.post("/remediations/rem_ffffffffffffffff/verify-recovery", json={}).status_code
        == 404
    )


def test_verify_unexecuted_remediation_is_409(client: TestClient) -> None:
    body: dict[str, Any] = {
        "incident_id": _INCIDENT,
        "incident_severity": "HIGH",
        "recommended_action": {
            "action_type": "RESTART_SERVICE",
            "target_service": "orders-service",
        },
    }
    rid = client.post("/remediations", json=body).json()["remediation_id"]
    assert client.post(f"/remediations/{rid}/verify-recovery", json={}).status_code == 409


def test_verify_approved_but_not_executed_is_409(client: TestClient) -> None:
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
        f"/remediations/{rid}/approve",
        json={"approver_identity": "a", "approver_role": "ADMINISTRATOR"},
    )
    assert client.post(f"/remediations/{rid}/verify-recovery", json={}).status_code == 409


# --- idempotency --------------------------------------------
def test_repeated_verification_replays_same_result(client: TestClient) -> None:
    rid = _executed(client)
    first = client.post(f"/remediations/{rid}/verify-recovery", json={}).json()
    second = client.post(f"/remediations/{rid}/verify-recovery", json={})
    assert second.status_code == 200
    assert (
        second.json()["verification"]["verification_id"] == first["verification"]["verification_id"]
    )
    # audit trail has exactly one verification-started and one verification-succeeded
    events = client.get(f"/remediations/{rid}/audit").json()["events"]
    types = [e["event_type"] for e in events]
    assert types.count("VERIFICATION_STARTED") == 1
    assert types.count("VERIFICATION_SUCCEEDED") == 1


# --- security payloads ------------------------------------
@pytest.mark.parametrize(
    "poison",
    [
        {"command": "kubectl delete pod orders-0"},
        {"script": "rm -rf /"},
        {"dry_run": True},
        {"force": True},
        {"target": "prod-cluster"},
        {"verifier": "SSHProbe"},
        {"max_error_rate": 1.0},
    ],
)
def test_verify_recovery_rejects_any_field(client: TestClient, poison: dict[str, Any]) -> None:
    rid = _executed(client)
    assert client.post(f"/remediations/{rid}/verify-recovery", json=poison).status_code == 422


def test_verify_recovery_body_accepts_only_empty_object(client: TestClient) -> None:
    rid = _executed(client)
    assert client.post(f"/remediations/{rid}/verify-recovery", json={}).status_code == 200


@pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
def test_verify_recovery_only_accepts_post(client: TestClient, method: str) -> None:
    rid = _executed(client)
    resp = client.request(method.upper(), f"/remediations/{rid}/verify-recovery")
    assert resp.status_code in (404, 405)


def test_verify_recovery_audit_events_present(client: TestClient) -> None:
    rid = _executed(client)
    client.post(f"/remediations/{rid}/verify-recovery", json={})
    types = [e["event_type"] for e in client.get(f"/remediations/{rid}/audit").json()["events"]]
    assert types[-2:] == ["VERIFICATION_STARTED", "VERIFICATION_SUCCEEDED"]


def test_metrics_expose_recovery_instrument(client: TestClient) -> None:
    rid = _executed(client)
    client.post(f"/remediations/{rid}/verify-recovery", json={})
    body = client.get("/metrics").text
    assert (
        "remediation_recovery_verifications" in body or "remediation.recovery.verifications" in body
    )
