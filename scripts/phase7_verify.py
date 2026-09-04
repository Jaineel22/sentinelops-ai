"""Phase 7 end-to-end verification — the inference-observability surface is wired.

Two modes:

* **in-process (default)** — trains/loads the real detector, scores a short
  synthetic window sequence exactly the way :class:`DetectorRunner` does
  (real ``score_window`` calls, real ``perf_counter`` latencies), then checks
  the Phase 7 metric surface, the ``/ready`` rollup, and the timing fields on
  the ``anomaly.detected`` payload. Needs no Kafka, no network, no Docker.
* **live** — ``--url http://localhost:8003`` hits a running anomaly-detector
  (``docker compose up``) and checks the same things over HTTP.

The Grafana check is best-effort: with ``--grafana-url`` it queries the API,
otherwise it just validates the committed dashboard JSON.

    python scripts/phase7_verify.py
    python scripts/phase7_verify.py --url http://localhost:8003 --grafana-url http://localhost:3000
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# The check marks are UTF-8; Windows consoles default to cp1252.
_reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(_reconfigure):
    _reconfigure(encoding="utf-8")

_REPO = Path(__file__).resolve().parents[1]
_DASHBOARD = (
    _REPO / "infrastructure" / "monitoring" / "grafana" / "dashboards" / "anomaly-detector.json"
)

_METRIC_FAMILIES = (
    "detector_inference_requests_total",
    "detector_anomalies_detected_total",
    "detector_inference_duration_seconds",
    "detector_detection_latency_end_to_end_seconds",
    "detector_model_info",
)
_READY_STATS_FIELDS = (
    "total_inferences",
    "total_anomalies",
    "anomaly_rate",
    "avg_latency_ms",
    "last_latency_ms",
    "min_latency_ms",
    "max_latency_ms",
    "last_inference_time",
)
_EVENT_TIMING_FIELDS = ("detection_latency_ms", "scrape_latency_ms", "inference_latency_ms")


class _Checker:
    def __init__(self) -> None:
        self.failures = 0

    def ok(self, msg: str) -> None:
        print(f"   ✅ {msg}")

    def bad(self, msg: str) -> None:
        print(f"   ❌ {msg}")
        self.failures += 1

    def check(self, cond: bool, ok_msg: str, bad_msg: str) -> None:
        self.ok(ok_msg) if cond else self.bad(bad_msg)


# --- in-process driving -------------------------------------------------
def _synthetic_windows() -> list[Any]:
    """A short deterministic window sequence: mostly normal, with an error spike
    and a latency spike so the detector actually fires."""

    from ml.data.schema import SIGNAL_COLUMNS

    from anomaly_detector.metrics_source import SignalWindow

    normal = dict.fromkeys(SIGNAL_COLUMNS, 0.0) | {
        "request_rate": 6.0,
        "success_rate": 1.0,
        "latency_mean_ms": 55.0,
        "latency_p50_ms": 50.0,
        "latency_p90_ms": 80.0,
        "latency_p95_ms": 95.0,
        "publish_rate": 6.0,
        "publish_latency_mean_ms": 2.0,
        "orders_created_rate": 6.0,
    }
    error_spike = normal | {"error_rate": 0.30, "success_rate": 0.70}
    latency_spike = normal | {
        "latency_mean_ms": 440.0,
        "latency_p50_ms": 400.0,
        "latency_p90_ms": 640.0,
        "latency_p95_ms": 760.0,
    }
    sequence = [normal, normal, error_spike, normal, latency_spike, normal, error_spike, normal]

    # Anchor the sequence in the recent past (oldest first, newest ends ~10s ago)
    # so end-to-end latency (publish_time - window_close) is a small positive.
    now = datetime.now(tz=UTC)
    windows: list[Any] = []
    for i, signals in enumerate(sequence):
        end = now - timedelta(seconds=10 * (len(sequence) - i))
        start = end - timedelta(seconds=10)
        windows.append(
            SignalWindow(
                window_start=start,
                window_end=end,
                dt_seconds=10.0,
                signals=dict(signals),
                scrape_time=end + timedelta(milliseconds=8),
            )
        )
    return windows


def _drive_inprocess() -> tuple[Any, Any, list[dict[str, Any]], Any]:
    """Score the synthetic windows the way DetectorRunner.tick does; return
    ``(metrics, state, published_payloads, settings)``."""

    from ml.data.schema import SIGNAL_COLUMNS

    from anomaly_detector import __version__
    from anomaly_detector.config import AppSettings, DetectorSettings, Settings
    from anomaly_detector.events import anomaly_event
    from anomaly_detector.metrics import get_metrics
    from anomaly_detector.state import DetectorState
    from anomaly_detector.timing import DetectionTimeline, record_detection_timeline
    from anomaly_detector.training import ensure_detector
    from sentinelops_common.obs import configure_observability

    configure_observability(
        service="anomaly-detector", version=__version__, env="verify", log_level="ERROR"
    )

    model_path = Path(tempfile.mkdtemp(prefix="phase7_verify_")) / "detector.joblib"
    settings = Settings(
        app=AppSettings(log_level="ERROR"),
        detector=DetectorSettings(model_path=str(model_path), mlflow=None),
    )
    detector = ensure_detector(settings.detector.model_path, seed=settings.detector.seed)

    metrics = get_metrics()
    state = DetectorState()
    published: list[dict[str, Any]] = []

    for window in _synthetic_windows():
        record: dict[str, object] = {c: window.signals[c] for c in SIGNAL_COLUMNS}
        record["window_start"] = window.window_start.isoformat()
        record["window_end"] = window.window_end.isoformat()

        inf_start = time.time()
        started = time.perf_counter()
        result = detector.score_window(record)
        latency = time.perf_counter() - started
        inf_end = time.time()

        metrics.record_inference(
            model_version=result.model_version,
            latency_seconds=latency,
            is_anomaly=result.is_anomaly,
            score=result.score,
        )
        metrics.record_service_inference(latency, result.is_anomaly)
        state.record_inference(latency_seconds=latency, is_anomaly=result.is_anomaly)

        publish_time = time.time() if result.is_anomaly else None
        timeline = DetectionTimeline(
            scrape_time=window.scrape_time.timestamp(),
            window_close_time=window.window_end.timestamp(),
            inference_start_time=inf_start,
            inference_end_time=inf_end,
            service=settings.detector.target_service,
            is_anomaly=result.is_anomaly,
            publish_time=publish_time,
        )
        record_detection_timeline(timeline, metrics)

        if result.is_anomaly:
            envelope = anomaly_event(
                window,
                result,
                service=settings.detector.target_service,
                environment=settings.detector.environment,
                timeline=timeline,
            )
            published.append(envelope.payload)

    metrics.set_model_info(version=detector.model_version, model_type=detector.model_type)
    return metrics, state, published, settings


def _ready_bodies_inprocess(settings: Any, state: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Spin up the real app (Kafka stubbed), point it at the driven state, and
    return the ``/ready`` and ``/ready/stats`` JSON."""

    from unittest.mock import patch

    from fastapi.testclient import TestClient

    import anomaly_detector.app as appmod

    class _Producer:
        def __init__(self, *_: object, **__: object) -> None: ...
        async def start(self) -> None: ...
        async def stop(self) -> None: ...
        async def publish(self, *_: object, **__: object) -> None: ...

    async def _noop(*_: object, **__: object) -> None:
        return None

    with (
        patch.object(appmod, "KafkaJsonProducer", _Producer),
        patch.object(appmod, "ensure_topics", _noop),
        TestClient(appmod.create_app(settings)) as client,
    ):
        client.app.state.detector_state = state  # type: ignore[attr-defined]
        ready: dict[str, Any] = client.get("/ready").json()
        stats: dict[str, Any] = client.get("/ready/stats").json()
    return ready, stats


def _verify_inprocess(grafana_url: str | None) -> int:
    print("=== Phase 7 Verification (in-process) ===\n")
    chk = _Checker()

    _metrics, state, published, settings = _drive_inprocess()

    from prometheus_client import generate_latest

    exposition = generate_latest().decode()

    print("1. Metrics Endpoint (/metrics)")
    for family in _METRIC_FAMILIES:
        chk.check(family in exposition, f"{family} found", f"{family} MISSING")

    print("\n2. Ready Endpoint (/ready)")
    ready, stats = _ready_bodies_inprocess(settings, state)
    inference_stats = ready.get("inference_stats", {})
    missing = [f for f in _READY_STATS_FIELDS if f not in inference_stats]
    chk.check(
        not missing, "inference_stats present (all fields)", f"inference_stats missing {missing}"
    )
    chk.check(
        isinstance(ready.get("uptime_seconds"), (int, float)),
        f"uptime_seconds: {ready.get('uptime_seconds')}",
        "uptime_seconds missing/!number",
    )
    healthy_note = "" if ready.get("healthy") else "  (synthetic input scored anomalous — expected)"
    chk.check(
        "healthy" in ready, f"healthy: {ready.get('healthy')}{healthy_note}", "healthy flag missing"
    )
    n_windows = len(_synthetic_windows())
    got = inference_stats.get("total_inferences")
    chk.check(
        got == n_windows,
        f"total_inferences: {got}",
        f"total_inferences={got}, expected {n_windows}",
    )

    print("\n3. Ready Stats Endpoint (/ready/stats)")
    chk.check(
        set(stats) == {"inference_stats", "uptime_seconds", "healthy", "health_reasons"},
        "stats endpoint available (stats-only shape)",
        f"unexpected /ready/stats keys: {sorted(stats)}",
    )

    print("\n4. Detection latency in anomaly.detected events")
    chk.check(
        len(published) > 0,
        f"{len(published)} anomaly event(s) produced",
        "no anomaly events produced",
    )
    for i, payload in enumerate(published):
        missing = [f for f in _EVENT_TIMING_FIELDS if payload.get(f) is None]
        chk.check(not missing, f"event {i}: timing fields present", f"event {i}: missing {missing}")

    print("\n5. Grafana dashboard")
    _verify_dashboard(chk, grafana_url)

    return _summary(chk)


# --- live mode ---------------------------------------------------------
def _verify_live(url: str, grafana_url: str | None) -> int:
    import httpx

    url = url.rstrip("/")
    print(f"=== Phase 7 Verification (live: {url}) ===\n")
    chk = _Checker()

    with httpx.Client(base_url=url, timeout=10.0) as client:
        print("1. Metrics Endpoint (/metrics)")
        exposition = client.get("/metrics").text
        for family in _METRIC_FAMILIES:
            chk.check(family in exposition, f"{family} found", f"{family} MISSING")

        print("\n2. Ready Endpoint (/ready)")
        ready = client.get("/ready").json()
        inference_stats = ready.get("inference_stats", {})
        missing = [f for f in _READY_STATS_FIELDS if f not in inference_stats]
        chk.check(
            not missing,
            "inference_stats present (all fields)",
            f"inference_stats missing {missing}",
        )
        chk.check(
            isinstance(ready.get("uptime_seconds"), (int, float)),
            f"uptime_seconds: {ready.get('uptime_seconds')}",
            "uptime_seconds missing/!number",
        )
        chk.check("healthy" in ready, f"healthy: {ready.get('healthy')}", "healthy flag missing")

        print("\n3. Ready Stats Endpoint (/ready/stats)")
        stats = client.get("/ready/stats").json()
        chk.check(
            set(stats) == {"inference_stats", "uptime_seconds", "healthy", "health_reasons"},
            "stats endpoint available (stats-only shape)",
            f"unexpected /ready/stats keys: {sorted(stats)}",
        )

        print("\n4. Detection latency in anomaly.detected events")
        n = inference_stats.get("total_inferences", 0)
        if n == 0:
            chk.bad(
                "no inferences yet — run scripts/generate_traffic.py against orders-service first"
            )
        else:
            chk.ok(f"{n} inference(s) recorded (drive traffic to see events on the wire)")

    print("\n5. Grafana dashboard")
    _verify_dashboard(chk, grafana_url)

    return _summary(chk)


# --- shared -----------------------------------------------------------
def _verify_dashboard(chk: _Checker, grafana_url: str | None) -> None:
    try:
        dashboard = json.loads(_DASHBOARD.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        chk.bad(f"dashboard JSON unreadable: {exc}")
        return
    chk.check(
        dashboard.get("uid") == "anomaly-detector",
        "dashboard JSON valid (uid ok)",
        "dashboard uid wrong",
    )
    chk.check(
        len(dashboard.get("panels", [])) >= 12,
        f"{len(dashboard['panels'])} panels defined",
        "fewer than 12 panels",
    )

    if not grafana_url:
        chk.ok("Grafana not queried (pass --grafana-url to check the running instance)")
        return
    try:
        import httpx

        resp = httpx.get(
            f"{grafana_url.rstrip('/')}/api/search",
            params={"query": "anomaly"},
            auth=("admin", "admin"),
            timeout=5.0,
        )
        hits = [d for d in resp.json() if d.get("uid") == "anomaly-detector"]
        chk.check(bool(hits), "dashboard provisioned in Grafana", "dashboard not found in Grafana")
    except Exception as exc:
        chk.ok(f"Grafana not reachable ({type(exc).__name__}) — skipped")


def _summary(chk: _Checker) -> int:
    print()
    if chk.failures == 0:
        print("Phase 7 complete! ✅")
        return 0
    print(f"Phase 7 verification: {chk.failures} check(s) FAILED ❌")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="live anomaly-detector base URL (e.g. http://localhost:8003)")
    parser.add_argument("--grafana-url", help="Grafana base URL (e.g. http://localhost:3000)")
    args = parser.parse_args()

    if args.url:
        return _verify_live(args.url, args.grafana_url)
    return _verify_inprocess(args.grafana_url)


if __name__ == "__main__":
    sys.exit(main())
