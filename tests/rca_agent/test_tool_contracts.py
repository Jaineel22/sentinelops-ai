"""Typed, bounded request contracts for the evidence tools (ADR-020)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from rca_agent.tools.contracts import (
    GetAnomalyEvidenceRequest,
    GetIncidentRequest,
    GetRelatedIncidentsRequest,
    GetServiceHealthRequest,
    GetServiceMetricsRequest,
)


@pytest.mark.parametrize(
    "incident_id",
    ["inc_0011223344556677", "inc_abcdef", "inc_deadbeefdeadbeef"],
)
def test_valid_incident_ids_accepted(incident_id: str) -> None:
    assert GetIncidentRequest(incident_id=incident_id).incident_id == incident_id


@pytest.mark.parametrize(
    "incident_id",
    [
        "abc123",
        "inc_XYZ",
        "inc_../../etc/passwd",
        "inc_0011; DROP TABLE incidents",
        "inc_" + "a" * 40,
        "INC_00112233",
        "",
    ],
)
def test_malformed_incident_ids_rejected(incident_id: str) -> None:
    with pytest.raises(ValidationError):
        GetIncidentRequest(incident_id=incident_id)


def test_requests_forbid_unexpected_fields() -> None:
    with pytest.raises(ValidationError):
        GetIncidentRequest.model_validate({"incident_id": "inc_00112233", "url": "http://evil"})


def test_anomaly_evidence_limit_is_bounded() -> None:
    GetAnomalyEvidenceRequest(incident_id="inc_00112233", limit=50)
    with pytest.raises(ValidationError):
        GetAnomalyEvidenceRequest(incident_id="inc_00112233", limit=51)
    with pytest.raises(ValidationError):
        GetAnomalyEvidenceRequest(incident_id="inc_00112233", limit=0)
    with pytest.raises(ValidationError):
        GetAnomalyEvidenceRequest(incident_id="inc_00112233", limit=1_000_000)


def test_related_incidents_lookback_and_limit_bounded() -> None:
    GetRelatedIncidentsRequest(service="orders-service", lookback_hours=720, limit=50)
    with pytest.raises(ValidationError):
        GetRelatedIncidentsRequest(service="orders-service", lookback_hours=721)
    with pytest.raises(ValidationError):
        GetRelatedIncidentsRequest(service="orders-service", limit=200)
    with pytest.raises(ValidationError):
        GetRelatedIncidentsRequest(service="x" * 200)
    with pytest.raises(ValidationError):
        GetRelatedIncidentsRequest.model_validate({"service": "orders-service", "status": "BOGUS"})


def test_service_metrics_names_are_bounded_and_syntax_checked() -> None:
    GetServiceMetricsRequest(service="orders-service", metric_names=["orders_created_total"])
    with pytest.raises(ValidationError):
        GetServiceMetricsRequest(service="orders-service", metric_names=[])
    with pytest.raises(ValidationError):
        GetServiceMetricsRequest(
            service="orders-service", metric_names=[f"m{i}" for i in range(16)]
        )
    with pytest.raises(ValidationError):
        GetServiceMetricsRequest(service="orders-service", metric_names=["a" * 200])
    for bad in ["rm -rf /", "metric name", "$(whoami)", "a;b", "../x"]:
        with pytest.raises(ValidationError):
            GetServiceMetricsRequest(service="orders-service", metric_names=[bad])


def test_service_health_request_bounds_service_name() -> None:
    GetServiceHealthRequest(service="orders-service")
    with pytest.raises(ValidationError):
        GetServiceHealthRequest(service="")
    with pytest.raises(ValidationError):
        GetServiceHealthRequest(service="s" * 200)


def test_requests_are_frozen() -> None:
    req = GetIncidentRequest(incident_id="inc_00112233")
    with pytest.raises(ValidationError):
        req.incident_id = "inc_ffffffff"
