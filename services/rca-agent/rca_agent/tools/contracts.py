"""Strongly typed request / payload contracts for every evidence tool (ADR-020).

Request models are ``frozen`` + ``extra="forbid"`` and every field is bounded.
Validation happens here, *before* any backend is contacted — an invalid or
excessive request is rejected without a network call.

Payload models describe the normalized shape stored in ``Evidence.content``.
They use ``extra="ignore"`` so an additive change to the upstream Incident API
does not break the tool layer (a missing *required* field still raises).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Incident id format established in Phase 3 (`inc_` + secrets.token_hex(8) == 16 hex).
INCIDENT_ID_RE = r"^inc_[0-9a-f]{6,32}$"
_METRIC_NAME_RE = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
_TRACE_ID_RE = r"^[0-9a-f]{8,32}$"
_INCIDENT_STATUS = Literal["OPEN", "ACKNOWLEDGED", "INVESTIGATING", "MITIGATING", "RESOLVED"]

_MAX_LOOKBACK_HOURS = 720  # 30 days
_MAX_RESULT_LIMIT = 50


class _Request(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --- request models -------------------------------------------------
class GetIncidentRequest(_Request):
    incident_id: str = Field(pattern=INCIDENT_ID_RE)


class GetIncidentTimelineRequest(_Request):
    incident_id: str = Field(pattern=INCIDENT_ID_RE)


class GetAnomalyEvidenceRequest(_Request):
    incident_id: str = Field(pattern=INCIDENT_ID_RE)
    limit: int = Field(default=20, ge=1, le=_MAX_RESULT_LIMIT)


class GetRelatedIncidentsRequest(_Request):
    service: str = Field(min_length=1, max_length=128)
    lookback_hours: int = Field(default=168, ge=1, le=_MAX_LOOKBACK_HOURS)
    status: _INCIDENT_STATUS | None = None
    limit: int = Field(default=10, ge=1, le=_MAX_RESULT_LIMIT)


class GetServiceMetricsRequest(_Request):
    service: str = Field(min_length=1, max_length=128)
    metric_names: list[str] = Field(min_length=1, max_length=15)

    @field_validator("metric_names")
    @classmethod
    def _bounded_metric_names(cls, value: list[str]) -> list[str]:
        for name in value:
            if len(name) > 120 or not _METRIC_NAME_RE.match(name):
                raise ValueError(f"invalid metric name: {name!r}")
        return value


class GetServiceHealthRequest(_Request):
    service: str = Field(min_length=1, max_length=128)


# --- request models for the *unavailable* sources ------------------
# Declared so the interface is well-defined and inputs are still validated; the
# tools never execute (they return SOURCE_UNAVAILABLE).
class GetRecentLogsRequest(_Request):
    service: str = Field(min_length=1, max_length=128)
    lookback_minutes: int = Field(default=30, ge=1, le=360)
    limit: int = Field(default=100, ge=1, le=500)
    contains: str | None = Field(default=None, max_length=200)  # a text filter, never code


class GetTracesRequest(_Request):
    service: str = Field(min_length=1, max_length=128)
    trace_id: str | None = Field(default=None, pattern=_TRACE_ID_RE)
    lookback_minutes: int = Field(default=30, ge=1, le=360)
    limit: int = Field(default=20, ge=1, le=100)


class GetRecentDeploymentsRequest(_Request):
    service: str = Field(min_length=1, max_length=128)
    lookback_hours: int = Field(default=48, ge=1, le=336)


class GetServiceDependenciesRequest(_Request):
    service: str = Field(min_length=1, max_length=128)
    direction: Literal["upstream", "downstream", "both"] = "both"


# --- payload models (normalized upstream data) ---------------------
class _Payload(BaseModel):
    model_config = ConfigDict(extra="ignore")


class IncidentPayload(_Payload):
    id: str
    correlation_key: str
    service: str
    environment: str
    status: str
    severity: str
    title: str
    anomaly_count: int
    distinct_abnormal_signals: int
    started_at: datetime
    last_evidence_at: datetime
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None
    acknowledged_at: datetime | None = None
    resolution: str | None = None
    severity_reasons: list[str] = Field(default_factory=list)
    abnormal_signal_names: list[str] = Field(default_factory=list)
    max_anomaly_score: float = 0.0
    max_error_rate: float = 0.0
    max_latency_p95_ms: float = 0.0
    detector: str = ""
    duration_seconds: float = 0.0


class AnomalyEvidenceItem(_Payload):
    event_id: str
    detector: str
    detector_version: str
    anomaly_score: float
    threshold: float
    window_start: datetime
    window_end: datetime
    signals: dict[str, float] = Field(default_factory=dict)
    abnormal_signals: list[str] = Field(default_factory=list)
    trace_id: str | None = None
    occurred_at: datetime
    correlation_reason: str


class IncidentTransitionItem(_Payload):
    from_status: str | None = None
    to_status: str
    actor: str
    reason: str
    severity_at_transition: str | None = None
    created_at: datetime


class RelatedIncidentItem(_Payload):
    id: str
    service: str
    environment: str
    status: str
    severity: str
    title: str
    anomaly_count: int
    started_at: datetime
    created_at: datetime
    resolved_at: datetime | None = None


class MetricSample(BaseModel):
    model_config = ConfigDict(frozen=True)

    labels: dict[str, str] = Field(default_factory=dict)
    value: float


class ServiceMetricSeries(BaseModel):
    model_config = ConfigDict(frozen=True)

    metric_name: str
    present: bool
    samples: list[MetricSample] = Field(default_factory=list)


class ServiceMetricsPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    service: str
    scraped_at: datetime
    note: str = "point-in-time scrape; no historical time range is queryable"
    series: list[ServiceMetricSeries] = Field(default_factory=list)


class ServiceHealthPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    service: str
    checked_at: datetime
    health: Literal["ok", "down", "unknown"]
    readiness: Literal["ok", "not_ready", "down", "unknown"]
    detail: dict[str, str] = Field(default_factory=dict)
