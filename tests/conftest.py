"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from sentinelops_api.config import Settings
from sentinelops_api.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(settings=Settings(env="test"))
    with TestClient(app) as test_client:
        yield test_client
