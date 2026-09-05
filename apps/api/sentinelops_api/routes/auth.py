"""Auth routes (Phase 10.1) — ``POST /api/v1/auth/login``, ``GET .../me``,
``POST .../register`` (admin only).

Mounted under ``/api/v1/auth`` — the only ``/api/v1`` surface this API exposes
today. It authenticates the operator dashboard's login screen
(``apps/frontend``); it does **not** authenticate the incident / RCA /
remediation / detector services, which remain unauthenticated internal
services by design (see ``sentinelops_api.auth`` module docstring).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from sentinelops_api.auth import (
    Role,
    TokenResponse,
    UserOut,
    UserRecord,
    authenticate_user,
    create_access_token,
    create_user_record,
    get_current_active_user,
    get_user_store,
    require_role,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=8, max_length=256)
    role: Role = Role.VIEWER


def _to_out(user: UserRecord) -> UserOut:
    return UserOut(username=user.username, role=user.role, disabled=user.disabled)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request) -> TokenResponse:
    user = authenticate_user(body.username, body.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "incorrect username or password")
    settings = request.app.state.settings
    return create_access_token(subject=user.username, role=user.role, settings=settings.auth)


@router.get("/me", response_model=UserOut)
def me(request: Request) -> UserOut:
    return _to_out(get_current_active_user(request))


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, request: Request) -> UserOut:
    """Admin-only. Adds a user to the in-memory store for this process's
    lifetime — there is no persistence layer for accounts (demo scope)."""

    require_role(request, Role.ADMIN)

    store = get_user_store()
    if store.get(body.username) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"user {body.username!r} already exists")
    record = create_user_record(body.username, body.password, body.role)
    store.add(record)
    return _to_out(record)
