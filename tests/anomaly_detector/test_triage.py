"""Coarse deterministic triage bands."""

from __future__ import annotations

from anomaly_detector.triage import abnormal_signals


def test_healthy_window_has_no_abnormal_signals() -> None:
    healthy = {
        "error_rate": 0.0,
        "latency_p95_ms": 45.0,
        "latency_mean_ms": 20.0,
        "publish_error_rate": 0.0,
        "publish_latency_mean_ms": 12.0,
    }
    assert abnormal_signals(healthy) == []


def test_latency_and_error_breaches_are_flagged_in_stable_order() -> None:
    bad = {"error_rate": 0.4, "latency_p95_ms": 900.0, "latency_mean_ms": 300.0}
    assert abnormal_signals(bad) == ["error_rate", "latency_p95_ms", "latency_mean_ms"]


def test_missing_signal_is_treated_as_zero() -> None:
    assert abnormal_signals({"error_rate": 0.2}) == ["error_rate"]
