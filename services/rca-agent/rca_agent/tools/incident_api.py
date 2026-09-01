"""Read-only HTTP client for the Phase 3 Incident API (ADR-019 / ADR-020).

The rca-agent talks to the incident-correlator **only** over this HTTP boundary
— it never imports ``incident_correlator.db`` / ``.repository``. The base URL is
fixed from configuration; callers supply only path segments built from
already-validated inputs (incident ids match ``INCIDENT_ID_RE``, so no traversal).
Only ``GET`` is ever issued.

Every failure mode maps to a typed error carrying a **sanitized** message
(no URL, host, path, or upstream exception text).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from pydantic import TypeAdapter, ValidationError

from rca_agent.tools.contracts import (
    AnomalyEvidenceItem,
    IncidentPayload,
    IncidentTransitionItem,
    RelatedIncidentItem,
)
from rca_agent.tools.results import ToolResultStatus

_EVIDENCE_LIST = TypeAdapter(list[AnomalyEvidenceItem])
_HISTORY_LIST = TypeAdapter(list[IncidentTransitionItem])
_SUMMARY_LIST = TypeAdapter(list[RelatedIncidentItem])


class IncidentApiError(RuntimeError):
    code: ToolResultStatus = ToolResultStatus.UPSTREAM_UNAVAILABLE

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class IncidentNotFound(IncidentApiError):
    code = ToolResultStatus.NOT_FOUND


class IncidentApiTimeout(IncidentApiError):
    code = ToolResultStatus.UPSTREAM_TIMEOUT


class IncidentApiUnavailable(IncidentApiError):
    code = ToolResultStatus.UPSTREAM_UNAVAILABLE


class IncidentApiMalformed(IncidentApiError):
    code = ToolResultStatus.MALFORMED_RESPONSE


class IncidentApiClient:
    def __init__(self, base_url: str, *, client: httpx.AsyncClient, timeout: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._client = client
        self._timeout = timeout

    async def _get_json(self, path: str, params: dict[str, str] | None = None) -> object:
        try:
            resp = await self._client.get(
                f"{self._base}{path}", params=params, timeout=self._timeout
            )
        except httpx.TimeoutException as exc:
            raise IncidentApiTimeout("the incident API request timed out") from exc
        except httpx.HTTPError as exc:
            raise IncidentApiUnavailable("could not reach the incident API") from exc

        if resp.status_code == httpx.codes.NOT_FOUND:
            raise IncidentNotFound("the incident was not found")
        if resp.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
            raise IncidentApiUnavailable("the incident API returned a server error")
        if resp.status_code != httpx.codes.OK:
            raise IncidentApiUnavailable(f"the incident API returned status {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise IncidentApiMalformed("the incident API returned a non-JSON response") from exc

    async def get_incident(self, incident_id: str) -> IncidentPayload:
        data = await self._get_json(f"/incidents/{incident_id}")
        try:
            return IncidentPayload.model_validate(data)
        except ValidationError as exc:
            raise IncidentApiMalformed(
                "the incident API response did not match the expected shape"
            ) from exc

    async def get_incident_evidence(self, incident_id: str) -> list[AnomalyEvidenceItem]:
        data = await self._get_json(f"/incidents/{incident_id}/evidence")
        try:
            return _EVIDENCE_LIST.validate_python(data)
        except ValidationError as exc:
            raise IncidentApiMalformed("the incident evidence response was malformed") from exc

    async def get_incident_history(self, incident_id: str) -> list[IncidentTransitionItem]:
        data = await self._get_json(f"/incidents/{incident_id}/history")
        try:
            return _HISTORY_LIST.validate_python(data)
        except ValidationError as exc:
            raise IncidentApiMalformed("the incident history response was malformed") from exc

    async def list_incidents(
        self,
        *,
        service: str,
        lookback_hours: int,
        status: str | None,
        limit: int,
    ) -> list[RelatedIncidentItem]:
        since = (datetime.now(tz=UTC) - timedelta(hours=lookback_hours)).isoformat()
        params = {"service": service, "since": since, "limit": str(limit)}
        if status is not None:
            params["status"] = status
        data = await self._get_json("/incidents", params=params)
        try:
            return _SUMMARY_LIST.validate_python(data)
        except ValidationError as exc:
            raise IncidentApiMalformed("the incident list response was malformed") from exc
