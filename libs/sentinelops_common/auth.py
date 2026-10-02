"""Shared JWT authentication and role checks for SentinelOps services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel, SecretStr


@dataclass(frozen=True)
class AuthConfig:
    secret_key: SecretStr
    algorithm: str = "HS256"


@dataclass(frozen=True)
class AuthenticatedUser:
    username: str
    role: str


class _Claims(BaseModel):
    sub: str
    role: str


_config: AuthConfig | None = None


def init_auth(config: AuthConfig) -> AuthConfig:
    """Register the process JWT configuration and return it for app wiring."""

    global _config
    _config = config
    return config


def _get_config(request: Request) -> AuthConfig:
    config = getattr(request.app.state, "auth_config", None) or _config
    if config is None:
        raise RuntimeError("JWT authentication is not initialized")
    return config


def get_current_user(request: Request) -> AuthenticatedUser:
    """Validate the bearer JWT and return its signed identity and role."""

    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    config = _get_config(request)
    try:
        payload = jwt.decode(
            token.strip(),
            config.secret_key.get_secret_value(),
            algorithms=[config.algorithm],
        )
        claims = _Claims.model_validate(payload)
    except (jwt.PyJWTError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return AuthenticatedUser(username=claims.sub, role=claims.role)


def require_role(role: str) -> Callable[[AuthenticatedUser], AuthenticatedUser]:
    """Return a dependency requiring ``role`` or a higher platform role."""

    ranks = {"viewer": 0, "approver": 1, "admin": 2}
    if role not in ranks:
        raise ValueError(f"unknown role {role!r}")

    def dependency(
        user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    ) -> AuthenticatedUser:
        if ranks.get(user.role, -1) < ranks[role]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires role {role!r} or higher")
        return user

    return dependency


__all__ = [
    "AuthConfig",
    "AuthenticatedUser",
    "get_current_user",
    "init_auth",
    "require_role",
]
