"""Build the outbound ``anomaly.detected`` envelope."""

from __future__ import annotations

from ml.inference import AnomalyResult

from anomaly_detector import SERVICE_NAME
from anomaly_detector.metrics_source import SignalWindow
from anomaly_detector.triage import abnormal_signals
from sentinelops_common.contracts import (
    ANOMALY_DETECTED,
    ANOMALY_DETECTED_VERSION,
    AnomalyDetectedV1,
)
from sentinelops_common.events import EventEnvelope
from sentinelops_common.obs import current_trace_id


def anomaly_event(
    window: SignalWindow, result: AnomalyResult, *, service: str, environment: str
) -> EventEnvelope:
    payload = AnomalyDetectedV1(
        detector=result.model_type,
        detector_version=result.model_version,
        service=service,
        environment=environment,
        window_start=window.window_start.isoformat(),
        window_end=window.window_end.isoformat(),
        anomaly_score=result.score,
        threshold=result.threshold,
        is_anomaly=result.is_anomaly,
        signals={k: round(float(v), 6) for k, v in window.signals.items()},
        abnormal_signals=abnormal_signals(window.signals),
    )
    return EventEnvelope(
        event_type=ANOMALY_DETECTED,
        event_version=ANOMALY_DETECTED_VERSION,
        occurred_at=window.window_end,
        source=SERVICE_NAME,
        trace_id=current_trace_id(),
        payload=payload.model_dump(),
    )
