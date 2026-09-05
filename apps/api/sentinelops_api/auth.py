"""JWT authentication + RBAC for the platform API (Phase 10.1).

Demo-grade, scoped to ``apps/api`` only (ADR-003: the rest of the platform —
incident-correlator, rca-agent, remediation-controller, anomaly-detector — is
internal and stays unauthenticated by design; this module does not change
them). Three hardcoded users cover the three roles the frontend gates on:

    admin    / admin123     -> Role.ADMIN
    approver / approver123  -> Role.APPROVER
    viewer   / viewer123    -> Role.VIEWER

Passwords are hashed at import time (never stored or compared in plain text)
with a fixed-iteration PBKDF2-HMAC — adequate for a demo credential set, not a
production KDF policy. ``require_role`` encodes a linear hierarchy
(``admin > approver > viewer``): ``require_role(Role.APPROVER)`` accepts both
``approver`` and ``admin``.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import jwt
from fastapi import HTTPException, Request, status
from pydantic import BaseModel

from sentinelops_api.config import AuthSettings

_PBKDF2_ITERATIONS = 200_000


class Role(StrEnum):
    VIEWER = "viewer"
    APPROVER = "approver"
    ADMIN = "admin"


_ROLE_RANK: dict[Role, int] = {Role.VIEWER: 0, Role.APPROVER: 1, Role.ADMIN: 2}


def _hash_password(password: str, *, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    salt_hex, _, digest_hex = stored.partition("$")
    if not salt_hex or not digest_hex:
        return False
    candidate = _hash_password(password, salt=bytes.fromhex(salt_hex))
    return hmac.compare_digest(candidate, stored)


@dataclass(frozen=True)
class UserRecord:
    username: str
    role: Role
    password_hash: str
    disabled: bool = False


def create_user_record(username: str, password: str, role: Role) -> UserRecord:
    """Hash ``password`` and build a :class:`UserRecord`. Public so
    ``routes.auth.register`` can add a user without reaching into a private
    helper; also used to seed the three demo accounts below."""

    salt = hashlib.sha256(username.encode("utf-8")).digest()[:16]  # deterministic, demo only
    return UserRecord(
        username=username, role=role, password_hash=_hash_password(password, salt=salt)
    )


@dataclass
class _UserStore:
    """In-memory, process-lifetime user store. Demo only — not persisted."""

    users: dict[str, UserRecord] = field(
        default_factory=lambda: {
            u.username: u
            for u in (
                create_user_record("admin", "admin123", Role.ADMIN),
                create_user_record("approver", "approver123", Role.APPROVER),
                create_user_record("viewer", "viewer123", Role.VIEWER),
            )
        }
    )

    def get(self, username: str) -> UserRecord | None:
        return self.users.get(username)

    def add(self, record: UserRecord) -> None:
        self.users[record.username] = record


_STORE = _UserStore()


def get_user_store() -> _UserStore:
    """FastAPI-dependency-friendly accessor (also swappable in tests)."""

    return _STORE


def reset_user_store() -> None:
    """Test-only: restore the in-memory store to just the three demo users,
    undoing anything a test registered. The store is process-lifetime and
    module-global, so tests that call ``/register`` should reset it after."""

    global _STORE
    _STORE = _UserStore()


class UserOut(BaseModel):
    username: str
    role: Role
    disabled: bool = False


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class TokenData(BaseModel):
    sub: str
    role: Role


def authenticate_user(
    username: str, password: str, *, store: _UserStore | None = None
) -> UserRecord | None:
    """Verify credentials. Returns ``None`` for an unknown user, bad password,
    or a disabled account — deliberately the same outcome for all three so a
    caller can't enumerate valid usernames from the response."""

    record = (store or _STORE).get(username)
    if record is None or record.disabled:
        return None
    if not _verify_password(password, record.password_hash):
        return None
    return record


def create_access_token(
    *, subject: str, role: Role, settings: AuthSettings, expires_minutes: int | None = None
) -> TokenResponse:
    minutes = settings.access_token_expire_minutes if expires_minutes is None else expires_minutes
    now = datetime.now(tz=UTC)
    expires_at = now + timedelta(minutes=minutes)
    payload = {
        "sub": subject,
        "role": role.value,
        "iat": now,
        "exp": expires_at,
        "jti": secrets.token_hex(8),
    }
    token = jwt.encode(
        payload, settings.secret_key.get_secret_value(), algorithm=settings.algorithm
    )
    return TokenResponse(access_token=token, expires_in=minutes * 60)


class InvalidTokenError(Exception):
    """A bearer token is missing, malformed, expired, or names an unknown user."""


def decode_access_token(
    token: str, settings: AuthSettings, *, store: _UserStore | None = None
) -> UserRecord:
    try:
        payload = jwt.decode(
            token, settings.secret_key.get_secret_value(), algorithms=[settings.algorithm]
        )
        data = TokenData(sub=payload["sub"], role=payload["role"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise InvalidTokenError(str(exc)) from exc

    record = (store or _STORE).get(data.sub)
    if record is None or record.disabled or record.role != data.role:
        # role != data.role: the user's role changed (or the account is gone)
        # since the token was issued — fail closed rather than trust the token.
        raise InvalidTokenError(f"user {data.sub!r} no longer valid for this token")
    return record


# --- request-scoped helpers -----------------------------------------
# Plain functions called from inside a route body (the pattern the rest of the
# platform uses — see e.g. incident_correlator.api._repo/_load — rather than
# FastAPI's ``Depends(...)``-in-signature idiom).
def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization")
    if not header or not header.lower().startswith("bearer "):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return header.split(" ", 1)[1].strip()


def get_current_user(request: Request) -> UserRecord:
    token = _bearer_token(request)
    settings = request.app.state.settings
    try:
        return decode_access_token(token, settings.auth)
    except InvalidTokenError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def get_current_active_user(request: Request) -> UserRecord:
    user = get_current_user(request)
    if user.disabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "user account is disabled")
    return user


def require_role(request: Request, minimum: Role) -> UserRecord:
    """``403`` (not ``401``) if the caller is authenticated but ranked below
    ``minimum`` (``admin > approver > viewer``); otherwise the user record."""

    user = get_current_active_user(request)
    if _ROLE_RANK[user.role] < _ROLE_RANK[minimum]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"requires role {minimum.value!r} or higher")
    return user


__all__ = [
    "InvalidTokenError",
    "Role",
    "TokenData",
    "TokenResponse",
    "UserOut",
    "UserRecord",
    "authenticate_user",
    "create_access_token",
    "create_user_record",
    "decode_access_token",
    "get_current_active_user",
    "get_current_user",
    "get_user_store",
    "require_role",
    "reset_user_store",
]
