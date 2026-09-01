"""Prometheus exposition parsing + quantile estimation."""

from __future__ import annotations

import math

from ml.data.prometheus_parse import estimate_quantile_from_bucket_deltas, parse_metrics


def test_parses_real_sample(metrics_text: str) -> None:
    snap = parse_metrics(metrics_text)
    assert snap.http_post_count_total > 0
    assert snap.http_post_count_2xx + snap.http_post_count_5xx <= snap.http_post_count_total
    assert snap.orders_created_total > 0
    assert snap.publish_success_total > 0
    assert snap.http_post_latency_buckets  # non-empty
    # cumulative buckets are monotonic in `le`
    items = sorted(snap.http_post_latency_buckets.items())
    counts = [c for _, c in items]
    assert counts == sorted(counts)


def test_empty_text_is_all_zero() -> None:
    snap = parse_metrics("")
    assert snap.http_post_count_total == 0.0
    assert snap.orders_created_total == 0.0


def test_quantile_interpolates_within_bucket() -> None:
    # 100 obs uniformly: 50 in (0,10], 50 in (10,20]
    deltas = {0.0: 0.0, 10.0: 50.0, 20.0: 100.0, float("inf"): 100.0}
    assert math.isclose(estimate_quantile_from_bucket_deltas(deltas, 0.5), 10.0, rel_tol=1e-6)
    assert 10.0 < estimate_quantile_from_bucket_deltas(deltas, 0.75) <= 20.0


def test_quantile_empty_or_zero_is_nan() -> None:
    assert math.isnan(estimate_quantile_from_bucket_deltas({}, 0.5))
    assert math.isnan(estimate_quantile_from_bucket_deltas({10.0: 0.0, float("inf"): 0.0}, 0.5))
