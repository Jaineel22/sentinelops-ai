"""Cross-service contract tests for the shared JWT dependency."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from sentinelops_common.auth import (
    AuthConfig,
    AuthenticatedUser,
    get_current_user,
    init_auth,
    require_role,
)

SECRET = SecretStr("phase-10-2-test-secret-key-32-bytes-min")


def _app() -> FastAPI:
    app = FastAPI()
    app.state.auth_config = init_auth(AuthConfig(secret_key=SECRET))

    @app.get("/read")
    def read(_: Annotated[AuthenticatedUser, Depends(get_current_user)]) -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/write")
    def write(
        _: Annotated[AuthenticatedUser, Depends(require_role("approver"))],
    ) -> dict[str, str]:
        return {"status": "ok"}

    return app


def _token(role: str = "approver", *, expired: bool = False) -> str:
    now = datetime.now(tz=UTC)
    return jwt.encode(
        {
            "sub": "alice",
            "role": role,
            "iat": now,
            "exp": now - timedelta(minutes=1) if expired else now + timedelta(minutes=5),
        },
        SECRET.get_secret_value(),
        algorithm="HS256",
    )


def test_unauthenticated_requests_return_401() -> None:
    with TestClient(_app()) as client:
        assert client.get("/read").status_code == 401
        assert client.post("/write").status_code == 401


def test_invalid_and_expired_tokens_return_401() -> None:
    with TestClient(_app()) as client:
        assert client.get("/read", headers={"Authorization": "Bearer invalid"}).status_code == 401
        assert (
            client.get(
                "/read", headers={"Authorization": f"Bearer {_token(expired=True)}"}
            ).status_code
            == 401
        )


def test_valid_token_with_wrong_role_returns_403() -> None:
    with TestClient(_app()) as client:
        response = client.post(
            "/write", headers={"Authorization": f"Bearer {_token(role='viewer')}"}
        )
        assert response.status_code == 403


def test_valid_token_is_accepted() -> None:
    with TestClient(_app()) as client:
        response = client.post("/write", headers={"Authorization": f"Bearer {_token()}"})
        assert response.status_code == 200
