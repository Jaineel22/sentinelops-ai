"""FastAPI application entry point.

Phase 0: health check + a root informational endpoint. Phase 10.1 adds JWT
auth (``/api/v1/auth/*``) for the operator dashboard's login screen — see
``sentinelops_api.auth``. Everything else deliberately contains no messaging,
persistence, ML, or agent logic; those live in ``services/``.
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from sentinelops_api import __version__
from sentinelops_api.config import Settings, get_settings
from sentinelops_api.routes.auth import router as auth_router


class HealthResponse(BaseModel):
    status: str


class RootResponse(BaseModel):
    name: str
    version: str
    phase: str
    docs: str


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory.

    Accepting an optional ``settings`` argument keeps the app testable and makes
    later dependency wiring explicit.
    """

    settings = settings or get_settings()

    app = FastAPI(
        title="SentinelOps AI API",
        version=__version__,
        summary="Incident intelligence platform API (Phase 0 foundation).",
    )
    app.state.settings = settings

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        """Liveness/readiness probe used by local dev, Docker, and future k8s."""

        return HealthResponse(status="ok")

    @app.get("/", response_model=RootResponse, tags=["system"])
    def root() -> RootResponse:
        return RootResponse(
            name="SentinelOps AI",
            version=__version__,
            phase="0 - Repository & Development Foundation",
            docs="/docs",
        )

    app.include_router(auth_router)

    return app


app = create_app()
