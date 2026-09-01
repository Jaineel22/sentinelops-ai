"""Deterministic end-to-end demo of Phase 3 incident correlation.

No Kafka, no database, no network — it wires the *real* domain code
(``AnomalyProcessor`` + the in-memory repository + the FastAPI app) and feeds it
a fixed, hand-authored sequence of anomaly signals, then queries the resulting
incident through the Incident API exactly as an operator (or Phase 4) would.

    python scripts/incident_scenario.py

Expected outcome: a healthy window is ignored; a latency anomaly opens ONE
incident; a second, related anomaly (rising error rate) is folded into the SAME
incident, escalating its severity; an unrelated service gets its own incident.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from incident_correlator.app import create_app
from incident_correlator.config import Settings
from incident_correlator.correlation import CorrelationConfig
from incident_correlator.domain import AnomalySignal
from incident_correlator.processor import AnomalyProcessor
from incident_correlator.repository import InMemoryIncidentRepository

_BASE = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


def _quiet() -> None:
    """This is a narrated demo — keep the platform's JSON logs out of the story."""

    for name in ("incident_correlator", "httpx", "httpcore", "opentelemetry"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _signal(
    *,
    service: str,
    offset_s: float,
    score: float,
    signals: dict[str, float],
    abnormal: list[str],
    event_id: str,
) -> AnomalySignal:
    start = _BASE + timedelta(seconds=offset_s)
    return AnomalySignal(
        event_id=event_id,
        detector="isolation_forest",
        detector_version="0.3.0",
        service=service,
        environment="development",
        window_start=start,
        window_end=start + timedelta(seconds=10),
        anomaly_score=score,
        threshold=0.5,
        signals=signals,
        abnormal_signals=abnormal,
        trace_id=None,
        occurred_at=start + timedelta(seconds=10),
    )


# A fixed timeline. Only the anomalous windows reach the processor — the detector
# would not emit an event for the healthy one; it is here to narrate the story.
_HEALTHY = {"error_rate": 0.0, "latency_p95_ms": 45.0, "request_rate": 5.0}
_TIMELINE = [
    (
        "orders-service latency spike",
        _signal(
            service="orders-service",
            offset_s=60,
            score=0.78,
            signals={"error_rate": 0.0, "latency_p95_ms": 620.0, "request_rate": 5.0},
            abnormal=["latency_p95_ms"],
            event_id="evt-1",
        ),
    ),
    (
        "orders-service errors climb (same incident, 90s later)",
        _signal(
            service="orders-service",
            offset_s=150,
            score=0.93,
            signals={"error_rate": 0.35, "latency_p95_ms": 540.0, "request_rate": 4.0},
            abnormal=["error_rate", "latency_p95_ms"],
            event_id="evt-2",
        ),
    ),
    (
        "replayed event (must be idempotent)",
        _signal(
            service="orders-service",
            offset_s=150,
            score=0.93,
            signals={"error_rate": 0.35, "latency_p95_ms": 540.0, "request_rate": 4.0},
            abnormal=["error_rate", "latency_p95_ms"],
            event_id="evt-2",
        ),
    ),
    (
        "payments-service unrelated anomaly (its own incident)",
        _signal(
            service="payments-service",
            offset_s=170,
            score=0.71,
            signals={"error_rate": 0.08, "latency_p95_ms": 300.0, "request_rate": 2.0},
            abnormal=["error_rate"],
            event_id="evt-3",
        ),
    ),
]


async def _run() -> InMemoryIncidentRepository:
    repo = InMemoryIncidentRepository()
    processor = AnomalyProcessor(repo, correlation_config=CorrelationConfig(window_seconds=300))

    print(f"healthy baseline window (ignored, no event emitted): {_HEALTHY}\n")
    for label, signal in _TIMELINE:
        outcome = await processor.process(signal)
        print(f"  {label}")
        print(
            f"    -> {outcome.result.value:9s} incident={outcome.incident_id} "
            f"({outcome.correlation_reason})"
        )
    print()
    return repo


def _report(repo: InMemoryIncidentRepository) -> None:
    app = create_app(Settings(), repository=repo, run_consumer=False)
    with TestClient(app) as client:
        incidents = client.get("/incidents").json()
        print(f"GET /incidents -> {len(incidents)} incident(s)\n")
        details = []
        for summary in incidents:
            detail = client.get(f"/incidents/{summary['id']}").json()
            details.append(detail)
            print(f"  {detail['id']}  [{detail['severity']}]  {detail['title']}")
            print(
                f"    status={detail['status']}  anomaly_count={detail['anomaly_count']}"
                f"  distinct_signals={detail['distinct_abnormal_signals']}"
            )
            print(f"    severity_reasons={detail['severity_reasons']}")
            print(
                f"    evidence={len(detail['evidence'])}  history="
                f"{[h['to_status'] for h in detail['history']]}"
            )
            print()

        orders = next(d for d in details if d["service"] == "orders-service")
        acked = client.post(f"/incidents/{orders['id']}/acknowledge").json()
        print(f"POST /incidents/{orders['id']}/acknowledge -> status={acked['status']}")
        history = client.get(f"/incidents/{orders['id']}/history").json()
        print(f"GET  /incidents/{orders['id']}/history -> {[h['to_status'] for h in history]}")

    assert len(incidents) == 2, "expected exactly two incidents"
    assert orders["anomaly_count"] == 2, "the two related anomalies must share one incident"
    assert orders["severity"] == "CRITICAL", "error rate 35% must drive severity to CRITICAL"
    print("\nOK: related anomalies correlated into ONE incident; replay was idempotent.")


if __name__ == "__main__":
    _quiet()
    _report(asyncio.run(_run()))
