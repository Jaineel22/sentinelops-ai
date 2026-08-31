"""Startup / smoke tests proving the application assembles and serves."""

from __future__ import annotations

from fastapi.testclient import TestClient

from sentinelops_api import __version__
from sentinelops_api.main import create_app


def test_app_factory_builds_app() -> None:
    app = create_app()
    assert app.title == "SentinelOps AI API"


def test_root_reports_phase_and_version(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "SentinelOps AI"
    assert body["version"] == __version__
    assert body["phase"].startswith("0")


def test_openapi_schema_is_served(client: TestClient) -> None:
    assert client.get("/openapi.json").status_code == 200
