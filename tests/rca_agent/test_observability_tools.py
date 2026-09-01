"""Service-metrics and service-health evidence tools (ADR-020)."""

from __future__ import annotations

import httpx

from rca_agent.tools.context import ToolContext
from rca_agent.tools.http_sources import ServiceHealthClient, ServiceMetricsClient
from rca_agent.tools.observability_tools import GetServiceHealthTool, GetServiceMetricsTool
from rca_agent.tools.results import ToolResultStatus
from tests.rca_agent.incident_api_fakes import METRICS_TEXT, make_mock_http

_METRICS_URLS = {"orders-service": "http://orders-test/metrics"}
_HEALTH_URLS = {"orders-service": "http://orders-test"}


def _metrics_client(handler: object) -> ServiceMetricsClient:
    return ServiceMetricsClient(_METRICS_URLS, client=make_mock_http(handler))  # type: ignore[arg-type]


def _health_client(handler: object) -> ServiceHealthClient:
    return ServiceHealthClient(_HEALTH_URLS, client=make_mock_http(handler))  # type: ignore[arg-type]


async def test_service_metrics_success(tool_context: ToolContext) -> None:
    tool = GetServiceMetricsTool(_metrics_client(lambda r: httpx.Response(200, text=METRICS_TEXT)))
    result = await tool.run(
        {"service": "orders-service", "metric_names": ["orders_created_total", "missing_metric"]},
        tool_context,
    )
    assert result.status is ToolResultStatus.OK
    payload = result.evidence[0].content
    series = {s["metric_name"]: s for s in payload["series"]}
    assert series["orders_created_total"]["present"] is True
    assert series["orders_created_total"]["samples"][0]["value"] == 42.0
    assert series["missing_metric"]["present"] is False  # honestly absent, not fabricated
    assert "point-in-time" in payload["note"]


async def test_service_metrics_unsupported_service_makes_no_request(
    tool_context: ToolContext,
) -> None:
    calls: list[str] = []

    def _spy(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text=METRICS_TEXT)

    tool = GetServiceMetricsTool(_metrics_client(_spy))
    result = await tool.run(
        {"service": "payments-service", "metric_names": ["x_total"]}, tool_context
    )
    assert result.status is ToolResultStatus.UNSUPPORTED_SERVICE
    assert calls == []


async def test_service_metrics_upstream_unavailable(tool_context: ToolContext) -> None:
    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    tool = GetServiceMetricsTool(_metrics_client(_boom))
    result = await tool.run(
        {"service": "orders-service", "metric_names": ["orders_created_total"]}, tool_context
    )
    assert result.status is ToolResultStatus.UPSTREAM_UNAVAILABLE


async def test_service_health_success(tool_context: ToolContext) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json={"status": "ready", "kafka": "connected"})

    tool = GetServiceHealthTool(_health_client(_handler))
    result = await tool.run({"service": "orders-service"}, tool_context)
    assert result.status is ToolResultStatus.OK
    payload = result.evidence[0].content
    assert payload["health"] == "ok"
    assert payload["readiness"] == "ok"
    assert payload["detail"]["kafka"] == "connected"


async def test_service_health_reports_not_ready(tool_context: ToolContext) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(503, json={"status": "not-ready", "kafka": "unavailable"})

    tool = GetServiceHealthTool(_health_client(_handler))
    result = await tool.run({"service": "orders-service"}, tool_context)
    assert result.status is ToolResultStatus.OK
    assert result.evidence[0].content["readiness"] == "not_ready"


async def test_service_health_unsupported_service(tool_context: ToolContext) -> None:
    tool = GetServiceHealthTool(_health_client(lambda r: httpx.Response(200, json={})))
    result = await tool.run({"service": "cassandra"}, tool_context)
    assert result.status is ToolResultStatus.UNSUPPORTED_SERVICE
