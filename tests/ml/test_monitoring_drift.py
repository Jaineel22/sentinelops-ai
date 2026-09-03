"""PSI drift detection (Phase 6D) - calculation, bands, controlled shifts."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from ml.monitoring.baseline import BaselineDistribution, freeze_baseline
from ml.monitoring.drift import (
    DriftReport,
    FeatureDriftReport,
    calculate_psi,
    classify_psi,
    compute_prediction_drift,
    detect_drift,
)

_FEATURES = ["latency_ms", "error_rate", "request_rate"]


def _frame(
    n: int, seed: int, *, latency_mean: float = 120.0, error_hi: float = 0.05
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "latency_ms": rng.normal(latency_mean, 20.0, n),
            "error_rate": rng.uniform(0.0, error_hi, n),
            "request_rate": rng.normal(6.0, 0.5, n),
        }
    )


def _baseline(n: int = 3000, seed: int = 1) -> BaselineDistribution:
    return freeze_baseline(
        _frame(n, seed), _FEATURES, model_version="1", feature_schema_version="1"
    )


# --- PSI maths --------------------------------------------------------------
def test_calculate_psi_identical_is_zero() -> None:
    assert calculate_psi([0.25, 0.25, 0.25, 0.25], [0.25, 0.25, 0.25, 0.25]) == pytest.approx(0.0)


def test_calculate_psi_correct() -> None:
    # (0.5-0.6)*ln(0.5/0.6) + (0.5-0.4)*ln(0.5/0.4) = 0.0182322 + 0.0223144
    assert calculate_psi([0.6, 0.4], [0.5, 0.5]) == pytest.approx(0.0405465, abs=1e-6)


def test_calculate_psi_handles_zeros() -> None:
    psi = calculate_psi([0.5, 0.5], [1.0, 0.0])
    assert np.isfinite(psi) and psi > 1.0


def test_calculate_psi_rejects_length_mismatch() -> None:
    with pytest.raises(ValueError):
        calculate_psi([0.5, 0.5], [1.0])


def test_classify_psi_bands() -> None:
    assert classify_psi(0.05) == "none"
    assert classify_psi(0.099) == "none"
    assert classify_psi(0.10) == "moderate"
    assert classify_psi(0.24) == "moderate"
    assert classify_psi(0.25) == "significant"
    assert classify_psi(1.5) == "significant"


def test_classify_psi_custom_thresholds() -> None:
    assert classify_psi(0.15, thresholds=(0.2, 0.4)) == "none"


# --- prediction drift ------------------------------------------------------
def test_compute_prediction_drift() -> None:
    assert compute_prediction_drift(0.10, 0.15) == pytest.approx(0.5)
    assert compute_prediction_drift(0.10, 0.05) == pytest.approx(-0.5)
    assert compute_prediction_drift(0.0, 0.5) is None


# --- detect_drift over controlled distributions ---------------------------
def test_no_drift_when_distributions_match() -> None:
    baseline = _baseline()
    report = detect_drift(_frame(3000, seed=99), baseline)  # same generator, new seed

    assert report.overall_decision == "no_drift"
    assert all(r.psi < 0.1 for r in report.feature_reports)
    assert all(r.decision == "pass" for r in report.feature_reports)


def test_drift_detected_on_controlled_shift() -> None:
    baseline = _baseline()
    shifted = _frame(3000, seed=42, latency_mean=155.0)  # +1.75 sigma on latency
    report = detect_drift(shifted, baseline)

    latency = next(r for r in report.feature_reports if r.feature_name == "latency_ms")
    assert latency.psi > 0.1
    assert latency.classification in ("moderate", "significant")
    assert report.overall_decision != "no_drift"


def test_significant_drift_detected() -> None:
    baseline = _baseline()
    shifted = _frame(3000, seed=7, latency_mean=260.0, error_hi=0.4)  # large shift
    report = detect_drift(shifted, baseline)

    latency = next(r for r in report.feature_reports if r.feature_name == "latency_ms")
    assert latency.psi >= 0.25
    assert latency.classification == "significant"
    assert report.overall_decision == "significant_drift"


def test_overall_decision_is_most_severe() -> None:
    baseline = _baseline()
    current = _frame(3000, seed=5)
    current["latency_ms"] = np.random.default_rng(0).normal(300.0, 20.0, len(current))  # blown out
    report = detect_drift(current, baseline)

    per_feature = {r.feature_name: r.classification for r in report.feature_reports}
    assert per_feature["latency_ms"] == "significant"
    assert report.overall_decision == "significant_drift"


def test_prediction_drift_is_a_separate_field() -> None:
    baseline = _baseline()
    report = detect_drift(
        _frame(2000, seed=11),
        baseline,
        prediction_rate_previous=0.20,
        prediction_rate_current=0.31,
    )
    assert report.prediction_drift == pytest.approx((0.31 - 0.20) / 0.20)
    # feature drift decision is unaffected by prediction drift
    assert report.overall_decision == "no_drift"


def test_detect_drift_returns_full_report() -> None:
    baseline = _baseline()
    report = detect_drift(_frame(1440, seed=3), baseline, model_version="7")

    assert isinstance(report, DriftReport)
    assert report.model_version == "7"
    assert report.n_samples_current == 1440
    assert report.timestamp
    assert len(report.feature_reports) == 3
    for r in report.feature_reports:
        assert isinstance(r, FeatureDriftReport)
        assert r.decision in ("pass", "warn", "fail")
        assert "mean" in r.reference_stats and "mean" in r.current_stats
    # fully JSON-serialisable, ASCII
    dumped = json.dumps(report.to_dict())
    assert dumped.isascii()


def test_detect_drift_with_missing_features() -> None:
    baseline = _baseline()
    partial = _frame(1000, seed=8).drop(columns=["request_rate"])
    report = detect_drift(partial, baseline)

    assert report.missing_features == ["request_rate"]
    assert {r.feature_name for r in report.feature_reports} == {"latency_ms", "error_rate"}


def test_detect_drift_rejects_empty_current() -> None:
    baseline = _baseline()
    with pytest.raises(ValueError):
        detect_drift(pd.DataFrame({c: [] for c in _FEATURES}), baseline)
