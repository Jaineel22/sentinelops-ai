"""Parse the Prometheus text exposition scraped from ``orders-service /metrics``
into the small set of aggregates the ML pipeline needs.

The service exposes cumulative counters and histograms (OpenTelemetry Prometheus
exporter). This module only *reads* a single scrape; turning consecutive scrapes
into per-window rates is :mod:`ml.data.prepare`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from prometheus_client.parser import text_string_to_metric_families

# Histogram upper bounds (``le``) we keep for latency-percentile estimation.
# These are the OpenTelemetry default millisecond buckets emitted for
# ``http.server.duration``.
HTTP_LATENCY_BUCKETS_MS: tuple[float, ...] = (
    0.0,
    5.0,
    10.0,
    25.0,
    50.0,
    75.0,
    100.0,
    250.0,
    500.0,
    750.0,
    1000.0,
    2500.0,
    5000.0,
    7500.0,
    10000.0,
)


@dataclass(frozen=True)
class MetricSnapshot:
    """Cumulative values from one scrape. All ``*_total``/``*_sum``/``*_count``
    fields are monotonic across the process lifetime (reset only on restart)."""

    # orders-service HTTP server, POST /orders only
    http_post_count_total: float = 0.0
    http_post_count_2xx: float = 0.0
    http_post_count_4xx: float = 0.0
    http_post_count_5xx: float = 0.0
    http_post_latency_sum_ms: float = 0.0
    http_post_latency_count: float = 0.0
    http_post_latency_buckets: Mapping[float, float] = field(default_factory=dict)

    # business + Kafka publish
    orders_created_total: float = 0.0
    publish_success_total: float = 0.0
    publish_failure_total: float = 0.0
    publish_latency_sum_s: float = 0.0
    publish_latency_count: float = 0.0

    # recorded for validation/debugging only — NEVER used as model features
    # (they encode the injected fault directly).
    debug_request_failed_simulated: float = 0.0
    debug_request_failed_publish: float = 0.0
    debug_failure_injection_total: float = 0.0


def _status_class(code: str) -> str:
    return f"{code[:1]}xx" if code[:1] in {"1", "2", "3", "4", "5"} else "other"


def parse_metrics(text: str) -> MetricSnapshot:
    """Parse one ``/metrics`` payload into a :class:`MetricSnapshot`."""

    http_count = {"2xx": 0.0, "4xx": 0.0, "5xx": 0.0, "total": 0.0}
    http_lat_sum = 0.0
    http_lat_count = 0.0
    buckets: dict[float, float] = {}
    created = 0.0
    pub_success = 0.0
    pub_failure = 0.0
    pub_lat_sum = 0.0
    pub_lat_count = 0.0
    dbg_sim = 0.0
    dbg_pub = 0.0
    dbg_inj = 0.0

    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            name, labels, value = sample.name, sample.labels, sample.value

            if name == "http_server_duration_milliseconds_count" and _is_orders_post(labels):
                http_count["total"] += value
                http_count[_status_class(labels.get("http_status_code", ""))] = (
                    http_count.get(_status_class(labels.get("http_status_code", "")), 0.0) + value
                )
            elif name == "http_server_duration_milliseconds_sum" and _is_orders_post(labels):
                http_lat_sum += value
            elif name == "http_server_duration_milliseconds_bucket" and _is_orders_post(labels):
                le = float(labels["le"]) if labels.get("le") not in (None, "+Inf") else float("inf")
                buckets[le] = buckets.get(le, 0.0) + value
                if le == float("inf"):
                    http_lat_count = max(http_lat_count, buckets[le])
            elif name == "orders_created_total":
                created += value
            elif name == "orders_publish_total":
                if labels.get("outcome") == "success":
                    pub_success += value
                elif labels.get("outcome") == "failure":
                    pub_failure += value
            elif name == "orders_publish_duration_seconds_sum":
                pub_lat_sum += value
            elif name == "orders_publish_duration_seconds_count":
                pub_lat_count += value
            elif name == "orders_request_failed_total":
                if labels.get("reason") == "simulated_error":
                    dbg_sim += value
                elif labels.get("reason") == "publish_failed":
                    dbg_pub += value
            elif name == "orders_failure_injection_total":
                dbg_inj += value

    return MetricSnapshot(
        http_post_count_total=http_count["total"],
        http_post_count_2xx=http_count.get("2xx", 0.0),
        http_post_count_4xx=http_count.get("4xx", 0.0),
        http_post_count_5xx=http_count.get("5xx", 0.0),
        http_post_latency_sum_ms=http_lat_sum,
        http_post_latency_count=http_lat_count,
        http_post_latency_buckets=buckets,
        orders_created_total=created,
        publish_success_total=pub_success,
        publish_failure_total=pub_failure,
        publish_latency_sum_s=pub_lat_sum,
        publish_latency_count=pub_lat_count,
        debug_request_failed_simulated=dbg_sim,
        debug_request_failed_publish=dbg_pub,
        debug_failure_injection_total=dbg_inj,
    )


def _is_orders_post(labels: Mapping[str, str]) -> bool:
    return labels.get("http_target") == "/orders" and labels.get("http_method") == "POST"


def estimate_quantile_from_bucket_deltas(
    bucket_deltas: Mapping[float, float], quantile: float
) -> float:
    """Estimate a quantile from per-window histogram bucket *deltas* by linear
    interpolation within the containing bucket (the same method Prometheus'
    ``histogram_quantile`` uses).

    ``bucket_deltas`` maps ``le`` upper bounds (may include ``inf``) to the
    count of observations in that cumulative bucket for the window.
    """

    if not bucket_deltas:
        return float("nan")
    ordered = sorted(bucket_deltas.items(), key=lambda kv: kv[0])
    total = ordered[-1][1]
    if total <= 0:
        return float("nan")
    target = quantile * total
    prev_le = 0.0
    prev_count = 0.0
    for le, cum in ordered:
        if cum >= target:
            if le == float("inf"):
                return prev_le
            span = cum - prev_count
            if span <= 0:
                return le
            return prev_le + (le - prev_le) * (target - prev_count) / span
        prev_le, prev_count = le, cum
    return ordered[-1][0]
