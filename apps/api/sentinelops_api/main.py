"""FastAPI application entry point.

Phase 0 only: this module wires up the application and exposes a health check
and a root informational endpoint. It deliberately contains no authentication,
messaging, persistence, ML, or agent logic.
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from sentinelops_api import __version__
from sentinelops_api.config import Settings, get_settings


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

    return app


app = create_app()
