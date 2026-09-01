"""Wiring helpers for the 4C investigation-engine tests (not collected)."""

from __future__ import annotations

import httpx

from rca_agent.config import RcaSettings, Settings
from rca_agent.engine import InvestigationService
from rca_agent.llm.base import LlmClient
from rca_agent.repository import InMemoryInvestigationRepository, InvestigationRepository
from rca_agent.tools import build_registry
from tests.rca_agent.incident_api_fakes import MockHandler


def settings_with(**rca_overrides: object) -> Settings:
    s = Settings()
    if rca_overrides:
        s.rca = RcaSettings(**{**s.rca.model_dump(), **rca_overrides})
    return s


def build_service(
    handler: MockHandler,
    *,
    settings: Settings | None = None,
    llm: LlmClient | None = None,
    repository: InvestigationRepository | None = None,
) -> InvestigationService:
    from rca_agent.llm import MockLlmClient

    s = settings or Settings()
    registry = build_registry(
        s, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    return InvestigationService(
        repository=repository or InMemoryInvestigationRepository(),
        registry=registry,
        llm_client=llm or MockLlmClient(),
        settings=s,
    )
