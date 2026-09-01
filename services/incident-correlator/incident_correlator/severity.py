"""Deterministic, rule-based severity engine.

No model, no LLM. Given an incident's aggregated evidence, evaluate a fixed set
of rules; the incident's severity is the **highest** level whose rule fires, and
every firing rule is recorded as a human-readable reason (stored on the incident
and handed to Phase 4).

All thresholds come from :class:`SeverityConfig` (``SEVERITY_*`` env vars), so an
operator can tune them without a code change.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_settings import BaseSettings, SettingsConfigDict

from incident_correlator.domain import Severity


class SeverityConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SEVERITY_", env_file=".env", extra="ignore")

    # MEDIUM
    anomaly_count_medium: int = 3
    distinct_signals_medium: int = 2
    # HIGH
    error_rate_high: float = 0.10
    latency_p95_high_ms: float = 500.0
    duration_high_seconds: float = 120.0
    # CRITICAL
    error_rate_critical: float = 0.30
    duration_critical_seconds: float = 300.0
    distinct_signals_critical: int = 2


@dataclass(frozen=True)
class SeverityInputs:
    anomaly_count: int
    distinct_abnormal_signals: int
    max_anomaly_score: float
    max_error_rate: float
    max_latency_p95_ms: float
    duration_seconds: float


@dataclass(frozen=True)
class SeverityVerdict:
    level: Severity
    reasons: list[str]


def evaluate_severity(
    inputs: SeverityInputs, config: SeverityConfig | None = None
) -> SeverityVerdict:
    cfg = config or SeverityConfig()
    fired: list[tuple[Severity, str]] = []

    if inputs.anomaly_count >= 1:
        fired.append((Severity.LOW, f"{inputs.anomaly_count} anomaly window(s)"))

    if inputs.anomaly_count >= cfg.anomaly_count_medium:
        fired.append((Severity.MEDIUM, f"sustained: >= {cfg.anomaly_count_medium} anomaly windows"))
    if inputs.distinct_abnormal_signals >= cfg.distinct_signals_medium:
        fired.append(
            (
                Severity.MEDIUM,
                f"{inputs.distinct_abnormal_signals} distinct signals abnormal",
            )
        )

    if inputs.max_error_rate >= cfg.error_rate_high:
        fired.append(
            (Severity.HIGH, f"error rate {inputs.max_error_rate:.0%} >= {cfg.error_rate_high:.0%}")
        )
    if inputs.max_latency_p95_ms >= cfg.latency_p95_high_ms:
        fired.append(
            (
                Severity.HIGH,
                f"p95 latency {inputs.max_latency_p95_ms:.0f}ms >= {cfg.latency_p95_high_ms:.0f}ms",
            )
        )
    if inputs.duration_seconds >= cfg.duration_high_seconds:
        fired.append(
            (
                Severity.HIGH,
                f"duration {inputs.duration_seconds:.0f}s >= {cfg.duration_high_seconds:.0f}s",
            )
        )

    if inputs.max_error_rate >= cfg.error_rate_critical:
        fired.append(
            (
                Severity.CRITICAL,
                f"error rate {inputs.max_error_rate:.0%} >= {cfg.error_rate_critical:.0%}",
            )
        )
    if (
        inputs.duration_seconds >= cfg.duration_critical_seconds
        and inputs.distinct_abnormal_signals >= cfg.distinct_signals_critical
    ):
        fired.append(
            (
                Severity.CRITICAL,
                f"multi-signal degradation sustained >= {cfg.duration_critical_seconds:.0f}s",
            )
        )

    if not fired:
        return SeverityVerdict(Severity.INFO, ["no rules fired"])

    top = max((level for level, _ in fired), key=lambda s: s.rank)
    reasons = [reason for level, reason in fired if level == top]
    return SeverityVerdict(top, reasons)
