"""Deterministic severity engine."""

from __future__ import annotations

from incident_correlator.domain import Severity
from incident_correlator.severity import SeverityConfig, SeverityInputs, evaluate_severity


def _inputs(**kw: float | int) -> SeverityInputs:
    base: dict[str, float | int] = {
        "anomaly_count": 0,
        "distinct_abnormal_signals": 0,
        "max_anomaly_score": 0.0,
        "max_error_rate": 0.0,
        "max_latency_p95_ms": 0.0,
        "duration_seconds": 0.0,
    }
    base.update(kw)
    return SeverityInputs(**base)  # type: ignore[arg-type]


def test_no_evidence_is_info() -> None:
    assert evaluate_severity(_inputs()).level == Severity.INFO


def test_single_anomaly_is_low() -> None:
    v = evaluate_severity(_inputs(anomaly_count=1))
    assert v.level == Severity.LOW
    assert v.reasons


def test_multiple_windows_escalate_to_medium() -> None:
    assert evaluate_severity(_inputs(anomaly_count=3)).level == Severity.MEDIUM


def test_two_distinct_signals_escalate_to_medium() -> None:
    assert evaluate_severity(_inputs(anomaly_count=1, distinct_abnormal_signals=2)).level == (
        Severity.MEDIUM
    )


def test_high_error_rate_is_high() -> None:
    v = evaluate_severity(_inputs(anomaly_count=1, max_error_rate=0.15))
    assert v.level == Severity.HIGH
    assert any("error rate" in r for r in v.reasons)


def test_slow_latency_is_high() -> None:
    assert (
        evaluate_severity(_inputs(anomaly_count=1, max_latency_p95_ms=600)).level == Severity.HIGH
    )


def test_long_duration_is_high() -> None:
    assert evaluate_severity(_inputs(anomaly_count=1, duration_seconds=200)).level == Severity.HIGH


def test_severe_error_rate_is_critical() -> None:
    assert (
        evaluate_severity(_inputs(anomaly_count=5, max_error_rate=0.35)).level == Severity.CRITICAL
    )


def test_sustained_multisignal_is_critical() -> None:
    v = evaluate_severity(
        _inputs(anomaly_count=10, distinct_abnormal_signals=3, duration_seconds=400)
    )
    assert v.level == Severity.CRITICAL


def test_takes_the_highest_firing_rule() -> None:
    # low + medium + high all fire; verdict is HIGH with only HIGH reasons
    v = evaluate_severity(_inputs(anomaly_count=4, distinct_abnormal_signals=2, max_error_rate=0.2))
    assert v.level == Severity.HIGH
    assert all("error rate" in r for r in v.reasons)


def test_thresholds_are_configurable() -> None:
    strict = SeverityConfig(error_rate_high=0.02)
    assert evaluate_severity(_inputs(anomaly_count=1, max_error_rate=0.03), strict).level == (
        Severity.HIGH
    )
