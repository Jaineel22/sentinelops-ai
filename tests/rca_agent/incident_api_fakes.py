"""Shared Incident-API fakes for the 4B evidence-tool tests (not collected)."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx

INCIDENT_API_BASE = "http://incident-test"
INCIDENT_ID = "inc_00112233aabbccdd"

MockHandler = Callable[[httpx.Request], httpx.Response]


def make_mock_http(handler: MockHandler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def ok(payload: object) -> httpx.Response:
    return httpx.Response(
        200, content=json.dumps(payload), headers={"content-type": "application/json"}
    )


INCIDENT_JSON: dict[str, object] = {
    "id": INCIDENT_ID,
    "correlation_key": "orders-service:development",
    "service": "orders-service",
    "environment": "development",
    "status": "OPEN",
    "severity": "HIGH",
    "title": "HIGH - error_rate, latency_p95_ms in orders-service (development)",
    "anomaly_count": 3,
    "distinct_abnormal_signals": 2,
    "started_at": "2026-09-01T12:00:00Z",
    "last_evidence_at": "2026-09-01T12:05:00Z",
    "created_at": "2026-09-01T12:01:00Z",
    "updated_at": "2026-09-01T12:05:00Z",
    "resolved_at": None,
    "acknowledged_at": None,
    "resolution": None,
    "severity_reasons": ["error rate 35% >= 30%"],
    "abnormal_signal_names": ["error_rate", "latency_p95_ms"],
    "max_anomaly_score": 0.93,
    "max_error_rate": 0.35,
    "max_latency_p95_ms": 780.0,
    "detector": "isolation_forest",
    "duration_seconds": 300.0,
    "evidence": [],
    "history": [],
}

EVIDENCE_JSON: list[dict[str, object]] = [
    {
        "event_id": f"evt-{i}",
        "detector": "isolation_forest",
        "detector_version": "0.3.0",
        "anomaly_score": 0.9,
        "threshold": 0.5,
        "window_start": "2026-09-01T12:00:00Z",
        "window_end": "2026-09-01T12:00:10Z",
        "signals": {"error_rate": 0.35, "latency_p95_ms": 780.0},
        "abnormal_signals": ["error_rate"],
        "trace_id": None,
        "occurred_at": "2026-09-01T12:00:10Z",
        "correlation_reason": "within correlation window (gap 10s <= 300s)",
    }
    for i in range(5)
]

HISTORY_JSON: list[dict[str, object]] = [
    {
        "from_status": None,
        "to_status": "OPEN",
        "actor": "system",
        "reason": "opened",
        "severity_at_transition": "LOW",
        "created_at": "2026-09-01T12:01:00Z",
    }
]

RELATED_JSON: list[dict[str, object]] = [
    {
        "id": "inc_ffeeddccbbaa9988",
        "correlation_key": "orders-service:development",
        "service": "orders-service",
        "environment": "development",
        "status": "RESOLVED",
        "severity": "MEDIUM",
        "title": "MEDIUM - latency_p95_ms in orders-service (development)",
        "anomaly_count": 2,
        "distinct_abnormal_signals": 1,
        "started_at": "2026-08-30T09:00:00Z",
        "last_evidence_at": "2026-08-30T09:03:00Z",
        "created_at": "2026-08-30T09:01:00Z",
        "updated_at": "2026-08-30T09:30:00Z",
        "resolved_at": "2026-08-30T09:30:00Z",
    }
]


def routing_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == f"/incidents/{INCIDENT_ID}":
        return ok(INCIDENT_JSON)
    if path == f"/incidents/{INCIDENT_ID}/evidence":
        return ok(EVIDENCE_JSON)
    if path == f"/incidents/{INCIDENT_ID}/history":
        return ok(HISTORY_JSON)
    if path == "/incidents":
        return ok(RELATED_JSON)
    return httpx.Response(404, content=json.dumps({"detail": "not found"}))


METRICS_TEXT = """
# HELP orders_created_total Orders successfully created.
# TYPE orders_created_total counter
orders_created_total 42.0
orders_request_failed_total{reason="server_error"} 5.0
process_cpu_seconds_total 3.14
"""

HEALTH_OK = {"status": "ok"}
READY_OK = {"status": "ready", "kafka": "connected"}


def make_incident(
    *,
    incident_id: str = INCIDENT_ID,
    service: str = "orders-service",
    severity: str = "HIGH",
    status: str = "OPEN",
    anomaly_count: int = 3,
    abnormal: list[str] | None = None,
) -> dict[str, object]:
    return {
        **INCIDENT_JSON,
        "id": incident_id,
        "service": service,
        "severity": severity,
        "status": status,
        "anomaly_count": anomaly_count,
        "distinct_abnormal_signals": len(abnormal or ["error_rate", "latency_p95_ms"]),
        "abnormal_signal_names": abnormal or ["error_rate", "latency_p95_ms"],
    }


def make_anomaly_windows(
    *, count: int, abnormal: list[str], score: float = 0.9, threshold: float = 0.5
) -> list[dict[str, object]]:
    return [
        {
            **EVIDENCE_JSON[0],
            "event_id": f"evt-{i}",
            "anomaly_score": score,
            "threshold": threshold,
            "abnormal_signals": abnormal,
            "window_start": f"2026-09-01T12:{i:02d}:00Z",
            "window_end": f"2026-09-01T12:{i:02d}:10Z",
        }
        for i in range(count)
    ]


def scenario_handler(
    *,
    incident_id: str = INCIDENT_ID,
    incident: dict[str, object] | None = None,
    anomalies: list[dict[str, object]] | None = None,
    related: list[dict[str, object]] | None = None,
    history: list[dict[str, object]] | None = None,
    poison_title: str | None = None,
) -> MockHandler:
    inc = dict(incident or make_incident(incident_id=incident_id))
    if poison_title is not None:
        inc["title"] = poison_title
    anomalies = (
        anomalies
        if anomalies is not None
        else make_anomaly_windows(count=3, abnormal=["error_rate", "latency_p95_ms"])
    )
    related = related if related is not None else []
    history = history if history is not None else HISTORY_JSON

    def _handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/incidents/{incident_id}":
            return ok(inc)
        if path == f"/incidents/{incident_id}/evidence":
            return ok(anomalies)
        if path == f"/incidents/{incident_id}/history":
            return ok(history)
        if path == "/incidents":
            return ok(related)
        if path == "/metrics":
            return httpx.Response(200, text=METRICS_TEXT)
        if path == "/health":
            return ok(HEALTH_OK)
        if path == "/ready":
            return ok(READY_OK)
        return httpx.Response(404, content=json.dumps({"detail": "not found"}))

    return _handler
