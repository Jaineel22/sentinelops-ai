"""JWT auth + RBAC contract tests (Phase 10.1, ``sentinelops_api.auth``).

Uses the same ``client`` fixture as the other apps/api tests (``tests/conftest.py``).
The user store is an in-memory, process-lifetime singleton, so every test that
registers a user resets it afterward via ``reset_user_store`` to stay isolated.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sentinelops_api.auth import reset_user_store


@pytest.fixture(autouse=True)
def _clean_user_store() -> Iterator[None]:
    yield
    reset_user_store()


def _login(client: TestClient, username: str, password: str) -> dict[str, Any]:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- login -------------------------------------------------------------
@pytest.mark.parametrize(
    ("username", "password", "role"),
    [
        ("admin", "admin123", "admin"),
        ("approver", "approver123", "approver"),
        ("viewer", "viewer123", "viewer"),
    ],
)
def test_login_succeeds_for_demo_users(
    client: TestClient, username: str, password: str, role: str
) -> None:
    body = _login(client, username, password)
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 30 * 60
    assert body["access_token"]

    me = client.get("/api/v1/auth/me", headers=_auth_header(body["access_token"]))
    assert me.status_code == 200
    assert me.json() == {"username": username, "role": role, "disabled": False}


def test_login_rejects_wrong_password(client: TestClient) -> None:
    resp = client.post("/api/v1/auth/login", json={"username": "admin", "password": "nope"})
    assert resp.status_code == 401


def test_login_rejects_unknown_user(client: TestClient) -> None:
    resp = client.post("/api/v1/auth/login", json={"username": "ghost", "password": "x"})
    assert resp.status_code == 401


def test_login_rejects_empty_body_fields(client: TestClient) -> None:
    resp = client.post("/api/v1/auth/login", json={"username": "", "password": ""})
    assert resp.status_code == 422


# --- /me -----------------------------------------------------------------
def test_me_requires_a_token(client: TestClient) -> None:
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


def test_me_rejects_garbage_token(client: TestClient) -> None:
    resp = client.get("/api/v1/auth/me", headers=_auth_header("not-a-jwt"))
    assert resp.status_code == 401


def test_me_rejects_token_for_a_role_that_no_longer_matches(client: TestClient) -> None:
    """A token minted for a role the user no longer has (e.g. demoted) is
    rejected rather than trusted — decode_access_token checks the live store."""
    from sentinelops_api.auth import Role, create_access_token
    from sentinelops_api.config import AuthSettings

    token = create_access_token(
        subject="viewer", role=Role.ADMIN, settings=AuthSettings()
    ).access_token
    resp = client.get("/api/v1/auth/me", headers=_auth_header(token))
    assert resp.status_code == 401


def test_expired_token_is_rejected(client: TestClient) -> None:
    from sentinelops_api.auth import Role, create_access_token
    from sentinelops_api.config import AuthSettings

    token = create_access_token(
        subject="viewer", role=Role.VIEWER, settings=AuthSettings(), expires_minutes=-1
    ).access_token
    resp = client.get("/api/v1/auth/me", headers=_auth_header(token))
    assert resp.status_code == 401


# --- RBAC (/register) ----------------------------------------------------
def test_register_requires_admin_role(client: TestClient) -> None:
    viewer = _login(client, "viewer", "viewer123")
    resp = client.post(
        "/api/v1/auth/register",
        json={"username": "someone", "password": "somepassword", "role": "viewer"},
        headers=_auth_header(viewer["access_token"]),
    )
    assert resp.status_code == 403


def test_approver_cannot_register(client: TestClient) -> None:
    approver = _login(client, "approver", "approver123")
    resp = client.post(
        "/api/v1/auth/register",
        json={"username": "someone", "password": "somepassword", "role": "viewer"},
        headers=_auth_header(approver["access_token"]),
    )
    assert resp.status_code == 403


def test_admin_can_register_a_new_user_who_can_then_log_in(client: TestClient) -> None:
    admin = _login(client, "admin", "admin123")
    created = client.post(
        "/api/v1/auth/register",
        json={"username": "opsuser", "password": "opsuserpassword", "role": "approver"},
        headers=_auth_header(admin["access_token"]),
    )
    assert created.status_code == 201
    assert created.json() == {"username": "opsuser", "role": "approver", "disabled": False}

    logged_in = _login(client, "opsuser", "opsuserpassword")
    me = client.get("/api/v1/auth/me", headers=_auth_header(logged_in["access_token"]))
    assert me.json()["role"] == "approver"


def test_register_rejects_a_duplicate_username(client: TestClient) -> None:
    admin = _login(client, "admin", "admin123")
    resp = client.post(
        "/api/v1/auth/register",
        json={"username": "admin", "password": "irrelevantpassword", "role": "viewer"},
        headers=_auth_header(admin["access_token"]),
    )
    assert resp.status_code == 409


def test_register_requires_a_token_at_all(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/auth/register",
        json={"username": "someone", "password": "somepassword", "role": "viewer"},
    )
    assert resp.status_code == 401


# --- role hierarchy end to end (admin > approver > viewer) ----------------
def test_role_hierarchy_admin_outranks_approver_which_outranks_viewer(
    client: TestClient,
) -> None:
    """``/register`` requires admin: viewer and approver are both rejected,
    only admin succeeds — exercising the same ``require_role`` gate the
    frontend's RBAC mirrors for approve/reject/execute."""

    for username, password in (("viewer", "viewer123"), ("approver", "approver123")):
        token = _login(client, username, password)["access_token"]
        resp = client.post(
            "/api/v1/auth/register",
            json={"username": f"blocked-by-{username}", "password": "somepassword"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 403, f"{username} should not be able to register a user"

    admin_token = _login(client, "admin", "admin123")["access_token"]
    resp = client.post(
        "/api/v1/auth/register",
        json={"username": "allowed-by-admin", "password": "somepassword"},
        headers=_auth_header(admin_token),
    )
    assert resp.status_code == 201
