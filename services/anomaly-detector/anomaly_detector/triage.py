"""Coarse, deterministic triage: which operational signals are outside their
normal band.

This is *not* the detection decision — the Isolation Forest model decides whether
a window is anomalous. These flags are a cheap, explainable annotation that
travels on the ``anomaly.detected`` event so the incident-correlator can name the
affected signals (and count distinct ones for severity) without re-deriving them.

Thresholds are intentionally generous and fixed; they describe "clearly
abnormal for this demo workload", calibrated against the ``run_a`` normal
segments (error rate ~0, p95 latency well under 200ms, publish errors ~0).
"""

from __future__ import annotations

from collections.abc import Mapping

# signal name -> (threshold, comparison is ">=")
_BANDS: dict[str, float] = {
    "error_rate": 0.05,  # >=5% of POST /orders returning 5xx
    "latency_p95_ms": 400.0,  # p95 server latency at/above 400ms
    "latency_mean_ms": 250.0,
    "publish_error_rate": 0.05,
    "publish_latency_mean_ms": 500.0,
}


def abnormal_signals(signals: Mapping[str, float]) -> list[str]:
    """Names of signals at/above their abnormal band, in a stable order."""

    return [name for name, limit in _BANDS.items() if float(signals.get(name, 0.0)) >= limit]
