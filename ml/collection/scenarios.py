"""Canonical operational scenarios and their ground-truth labels.

These mirror ``scripts/generate_traffic.py`` (Phase 1). They are duplicated here
rather than imported so the ML subsystem does not reach into ``scripts/``; a
test (``tests/ml/test_labeling.py``) asserts the two definitions stay in sync.

Label taxonomy
--------------
Multiclass ``label`` is the ground truth for analysis/plots. Binary
``is_anomaly`` is what the detectors are scored against.

* ``normal``           — baseline behaviour                     -> 0
* ``recovery``         — injection cleared, telemetry is normal  -> 0
* ``latency_anomaly``  — elevated request latency                -> 1
* ``error_anomaly``    — elevated 5xx rate                       -> 1
* ``publish_failure``  — Kafka publish failures (503s)           -> 1
* ``traffic_surge``    — ~4x request rate, no injected fault     -> 1

``traffic_surge`` is labelled anomalous: a sudden load change is an unusual
operating condition worth flagging even though nothing has "failed". This
choice is documented in docs/architecture/phase-2.md and is revisited if it
proves to hurt more than help.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    name: str
    label: str
    rate_multiplier: float
    latency_ms: int
    error_rate: float
    publish_error_rate: float

    @property
    def simulation_payload(self) -> dict[str, float | int]:
        return {
            "latency_ms": self.latency_ms,
            "error_rate": self.error_rate,
            "publish_error_rate": self.publish_error_rate,
        }


SCENARIOS: dict[str, Scenario] = {
    "normal": Scenario("normal", "normal", 1.0, 0, 0.0, 0.0),
    "latency": Scenario("latency", "latency_anomaly", 1.0, 400, 0.0, 0.0),
    "errors": Scenario("errors", "error_anomaly", 1.0, 0, 0.25, 0.0),
    "publish_failure": Scenario("publish_failure", "publish_failure", 1.0, 0, 0.0, 0.25),
    "surge": Scenario("surge", "traffic_surge", 4.0, 0, 0.0, 0.0),
    "recovery": Scenario("recovery", "recovery", 1.0, 0, 0.0, 0.0),
}

NORMAL_LABELS: frozenset[str] = frozenset({"normal", "recovery"})
ANOMALY_LABELS: frozenset[str] = frozenset(
    {"latency_anomaly", "error_anomaly", "publish_failure", "traffic_surge"}
)
ALL_LABELS: frozenset[str] = NORMAL_LABELS | ANOMALY_LABELS


def label_to_binary(label: str) -> int:
    if label in NORMAL_LABELS:
        return 0
    if label in ANOMALY_LABELS:
        return 1
    raise ValueError(f"unknown label: {label!r}")
