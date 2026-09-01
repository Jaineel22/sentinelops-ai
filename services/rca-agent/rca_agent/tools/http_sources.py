"""Read-only HTTP clients for instrumented-service ``/metrics`` and ``/health``.

Both are constrained to a fixed **allow-list** of services (the keys of the
``RCA_SERVICE_*_URLS`` maps). A service not in the map is rejected before any
request — the agent cannot point these at an arbitrary host. Only ``GET`` is
issued, with a bounded timeout.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from rca_agent.tools.contracts import (
    MetricSample,
    ServiceHealthPayload,
    ServiceMetricSeries,
    ServiceMetricsPayload,
)
from rca_agent.tools.prometheus import parse_exposition
from rca_agent.tools.results import ToolExecutionError, ToolResultStatus

_MAX_BODY_BYTES = 2_000_000


class UnknownService(ToolExecutionError):
    def __init__(self, service: str) -> None:
        super().__init__(
            ToolResultStatus.UNSUPPORTED_SERVICE,
            f"service {service!r} is not in the metrics/health allow-list",
        )


class ServiceMetricsClient:
    def __init__(
        self, urls: dict[str, str], *, client: httpx.AsyncClient, timeout: float = 10.0
    ) -> None:
        self._urls = dict(urls)
        self._client = client
        self._timeout = timeout

    def supports(self, service: str) -> bool:
        return service in self._urls

    async def scrape(self, service: str, metric_names: list[str]) -> ServiceMetricsPayload:
        url = self._urls.get(service)
        if url is None:
            raise UnknownService(service)
        try:
            resp = await self._client.get(url, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            raise ToolExecutionError(
                ToolResultStatus.UPSTREAM_TIMEOUT, f"scraping {service} metrics timed out"
            ) from exc
        except httpx.HTTPError as exc:
            raise ToolExecutionError(
                ToolResultStatus.UPSTREAM_UNAVAILABLE, f"could not reach {service} metrics"
            ) from exc
        if resp.status_code != httpx.codes.OK:
            raise ToolExecutionError(
                ToolResultStatus.UPSTREAM_UNAVAILABLE,
                f"{service} metrics returned status {resp.status_code}",
            )

        text = resp.text[:_MAX_BODY_BYTES]
        found = parse_exposition(text, set(metric_names))
        series = [
            ServiceMetricSeries(
                metric_name=name,
                present=name in found,
                samples=[MetricSample(labels=s.labels, value=s.value) for s in found.get(name, [])],
            )
            for name in metric_names
        ]
        return ServiceMetricsPayload(
            service=service, scraped_at=datetime.now(tz=UTC), series=series
        )


class ServiceHealthClient:
    def __init__(
        self, urls: dict[str, str], *, client: httpx.AsyncClient, timeout: float = 10.0
    ) -> None:
        self._urls = dict(urls)
        self._client = client
        self._timeout = timeout

    def supports(self, service: str) -> bool:
        return service in self._urls

    async def _probe(self, base: str, path: str) -> tuple[int | None, dict[str, str]]:
        try:
            resp = await self._client.get(f"{base.rstrip('/')}{path}", timeout=self._timeout)
        except httpx.HTTPError:
            return None, {}
        detail: dict[str, str] = {}
        try:
            body = resp.json()
            if isinstance(body, dict):
                detail = {str(k): str(v)[:120] for k, v in list(body.items())[:10]}
        except ValueError:
            detail = {}
        return resp.status_code, detail

    async def check(self, service: str) -> ServiceHealthPayload:
        base = self._urls.get(service)
        if base is None:
            raise UnknownService(service)

        health_code, health_detail = await self._probe(base, "/health")
        ready_code, ready_detail = await self._probe(base, "/ready")

        health = "unknown" if health_code is None else ("ok" if health_code == 200 else "down")
        if ready_code is None:
            readiness = "unknown"
        elif ready_code == 200:
            readiness = "ok"
        elif ready_code == 503:
            readiness = "not_ready"
        else:
            readiness = "down"

        return ServiceHealthPayload(
            service=service,
            checked_at=datetime.now(tz=UTC),
            health=health,  # type: ignore[arg-type]
            readiness=readiness,  # type: ignore[arg-type]
            detail={**health_detail, **ready_detail},
        )
