# Phase 7 — Real-Time ML Inference Observability

> Status: **complete.** All sub-phases are implemented, tested and documented:
> 7A (Prometheus inference metrics), 7B (detection-latency timeline), 7C
> (enhanced `/ready` + service aggregates), 7D (Prometheus + Grafana dashboard),
> 7E (documentation & verification). One-line recap:
> [docs/phase7-summary.md](../phase7-summary.md); `make phase7-verify`.
>
> Scope note: this phase instruments the **anomaly-detector's inference path**
> and stands up **Prometheus + Grafana**. Cross-service OpenTelemetry rollout and
> Loki / Tempo / an OTel Collector remain future work (the roadmap's original
> "full observability stack").

## 1. Overview

Phases 3–6 gave the anomaly-detector a real model, a registry, and a
scrape → score → publish loop — but almost no visibility into that loop at
runtime. Phase 7 makes the *inference path itself* observable: how much it is
doing, how fast, how often it fires, which model is live, and where the
end-to-end detection latency goes — surfaced both as a Prometheus metric surface
(ADR-007: OpenTelemetry instruments, exported for scrape at `GET /metrics`) and
as a provisioned Grafana dashboard.

No detection logic changed in any sub-phase. Every number on the dashboard comes
from an actual run.

```
score_window()  ─►  OTel instruments  ─►  /metrics (Prometheus exposition)
     │                     │
     │                     ├─► Prometheus (scrape every 5s, job "anomaly-detector")
     │                     │        │
     │                     │        └─► Grafana ("Anomaly Detector - Inference & Performance")
     │                     │
     └─► DetectorState  ─►  /ready  (inference_stats rollup + healthy flag)
```

## 2. Sub-phase 7A — Prometheus metrics for inference

`DetectorMetrics` (`services/anomaly-detector/anomaly_detector/metrics.py`) gains
an inference view, recorded once per scored window from `DetectorRunner.tick`:

| Instrument | Prometheus series | Labels |
| --- | --- | --- |
| `detector.inference.requests` | `detector_inference_requests_total` | `model_version` |
| `detector.inference.duration` | `detector_inference_duration_seconds_*` | `model_version` |
| `detector.anomalies.detected` | `detector_anomalies_detected_total` | `model_version` |
| `detector.anomaly.score` | `detector_anomaly_score_*` | `model_version` |
| `detector.model.version` / `.type` / `.info` | `detector_model_version` / `_type` / `_info` | (info: `model_version`, `model_type`) |

`detector.model.{version,type,info}` are **observable** gauges — their callbacks
are polled on every collection, so the series never goes stale in Prometheus
between model changes. (A synchronous gauge is only re-exported on the collection
right after a `set`, which Prometheus then marks stale — the model panels would
read "No data" within minutes.) `set_model_info(...)` (startup, and on any model
swap) just updates the values the callbacks return. Service name stays a resource
attribute (ADR-007), not a label.

## 3. Sub-phase 7B — detection latency timeline

`anomaly_detector/timing.py` keeps a small per-cycle `DetectionTimeline` of
epoch-second timestamps (scrape / window-close / inference start+end / publish)
and derives a latency breakdown:

| Instrument | Prometheus series | Meaning |
| --- | --- | --- |
| `detector.window.age_at_scrape` | `detector_window_age_at_scrape_seconds_*` | window close → scrape (≈ scrape I/O) |
| `detector.scrape.to.publish` | `detector_scrape_to_publish_seconds_*` | scrape → publish (published anomalies) |
| `detector.detection.latency.end_to_end` | `detector_detection_latency_end_to_end_seconds_*` | window close → publish |

All three carry an explicit sub-second bucket advisory. The same breakdown rides
along on the `anomaly.detected` payload as optional `detection_latency_ms` /
`scrape_latency_ms` / `inference_latency_ms` (debugging only — never correlation
logic).

## 4. Sub-phase 7C — enhanced `/ready` + service aggregates

`anomaly_detector/state.py` holds a thread-safe `DetectorState`: inference /
anomaly counts, an EMA of inference latency, min/max, last-inference time,
startup time. `/ready` now returns, alongside the unchanged
`status` / `model_*` fields:

```json
{
  "status": "ready",
  "model_loaded": true, "model_source": "registry", "model_version": "3",
  "model_type": "isolation_forest",
  "inference_stats": {
    "total_inferences": 1234, "total_anomalies": 56, "anomaly_rate": 4.54,
    "avg_latency_ms": 12.3, "last_latency_ms": 8.7,
    "min_latency_ms": 2.1, "max_latency_ms": 45.6,
    "last_inference_time": "2026-09-04T10:00:00+00:00"
  },
  "uptime_seconds": 3600.0,
  "healthy": true,
  "health_reasons": []
}
```

`healthy` / `health_reasons` are a **soft** degradation signal (thresholds in
`HealthSettings`, env prefix `HEALTH_`): stale (no inference for
`unhealthy_after_no_inference_seconds`), anomaly rate over
`unhealthy_if_anomaly_rate_above` %, or average latency over
`unhealthy_if_avg_latency_above_ms`. They **never** change the `/ready` HTTP
status — that stays tied to the scoring loop being alive. `GET /ready/stats`
returns just the stats portion.

Service-level aggregate metrics (unlabelled twins, for "this instance, all
models" panels): `detector_service_inference_requests_total`,
`detector_service_anomalies_detected_total`,
`detector_service_inference_latency_seconds_*`.

## 5. Sub-phase 7D — Prometheus + Grafana

`infrastructure/monitoring/`:

```
prometheus/prometheus.yml                              scrape config (job "anomaly-detector", 5s)
grafana/provisioning/datasources/prometheus.yaml      data source (uid "prometheus")
grafana/provisioning/dashboards/dashboards.yaml       file provider -> /etc/dashboards
grafana/dashboards/anomaly-detector.json              the dashboard (12 panels)
```

`docker compose up` starts `prometheus` (`:9090`) and `grafana` (`:3000`,
`admin`/`admin`). Both are best-effort — the app runs if they are down.

### Dashboard panels

| # | Type | Title | Query (essence) |
| --- | --- | --- | --- |
| 1 | stat | Total Inferences | `sum(increase(detector_inference_requests_total[$__range]))` |
| 2 | stat | Total Anomalies | `sum(increase(detector_anomalies_detected_total[$__range]))` |
| 3 | gauge | Anomaly Rate | anomalies / inferences × 100 over range; green <10 %, red >25 % |
| 4 | stat | Latest Inference Latency | `rate(..._sum[5m]) / rate(..._count[5m])` |
| 5 | stat | Current Model Version | `detector_model_info` → `{{model_version}}` |
| 6 | stat | Current Model Type | `detector_model_info` → `{{model_type}}` |
| 7 | stat | Service Uptime | `time() - process_start_time_seconds{job="anomaly-detector"}` |
| 8 | timeseries | Inference Requests / sec | `sum(rate(detector_inference_requests_total[1m]))` |
| 9 | timeseries | Anomalies Detected / sec | `sum(rate(detector_anomalies_detected_total[1m]))` |
| 10 | timeseries | Inference Latency (p50/p95/p99) | `histogram_quantile(q, sum(rate(detector_inference_duration_seconds_bucket[5m])) by (le))` |
| 11 | timeseries | End-to-End Detection Latency (p95) | `histogram_quantile(0.95, sum(rate(detector_detection_latency_end_to_end_seconds_bucket[5m])) by (le))` |
| 12 | heatmap | Anomaly Score Distribution | `sum(rate(detector_anomaly_score_bucket[5m])) by (le)` |

Two non-behavioral `metrics.py` changes landed here so the panels show real data:
explicit sub-second buckets on `inference.duration` + `anomaly.score` (panels 10
and 12), and the observable model gauges above. No detection logic was touched.

## 6. Sub-phase 7E — Documentation & Verification

- `scripts/phase7_verify.py` (`make phase7-verify`) — end-to-end check of the
  Phase 7 surface. **In-process** (default): trains/loads the real detector,
  scores a short synthetic window sequence the way `DetectorRunner.tick` does
  (real `score_window`, real `perf_counter` latencies), then asserts the metric
  families are exposed, `/ready` + `/ready/stats` carry the rollup, and the
  `anomaly.detected` payload has the timing fields. **Live**: `--url
  http://localhost:8003` runs the same checks over HTTP against a running
  detector; `--grafana-url` additionally checks the dashboard is provisioned.
- `tests/infrastructure/test_grafana_dashboard.py` (Phase 7D) — static
  validation of the dashboard JSON / provisioning YAML / compose wiring.
- Docs: this file, `docs/phase7-summary.md`, and the Phase 7 sections of
  `README.md`, `docs/architecture/overview.md`, `docs/phases/roadmap.md`,
  `docs/development/setup.md`.

## 7. How to look at it

```bash
docker compose up -d --build kafka orders-service anomaly-detector prometheus grafana
python scripts/generate_traffic.py --scenario sequence --duration 60 --rate 6

curl -s http://localhost:9090/api/v1/targets            # anomaly-detector: up
curl -s http://localhost:8003/ready | python -m json.tool
python scripts/phase7_verify.py --url http://localhost:8003 --grafana-url http://localhost:3000
# Grafana: http://localhost:3000  (admin/admin) -> Anomaly Detector - Inference & Performance
```

## 8. Known limitations

- **`detector_detection_latency_seconds` (7A) and
  `detector_service_inference_latency_seconds` (7C) still use the OTel default
  (whole-second) buckets.** Neither is on a dashboard percentile panel; the
  inference-duration and anomaly-score histograms *were* given explicit buckets
  in 7D because panels 10 and 12 depend on them.
- **Uptime panel needs `process_start_time_seconds`**, which `prometheus_client`
  only registers where `/proc` exists — present in the Linux container, absent
  when running the detector natively on Windows.
- **Only the anomaly-detector is instrumented / scraped.** The other services
  already expose `/metrics` in the same shape; adding scrape jobs + panels is
  incremental. Loki / Tempo / an OTel Collector are not part of Phase 7.
- **Prometheus/Grafana are not wired into CI**; 7D's tests are static config
  checks. End-to-end rendering is a `docker compose up` + `make phase7-verify`
  check, exercised during 7D/7E.

## 9. What Phase 7 delivered

The anomaly-detector's inference loop went from a black box to a fully observed
path: **14 new OpenTelemetry instruments** (inference throughput, latency
histograms with real buckets, anomaly counts + score distribution, a 3-hop
detection-latency breakdown, service-level aggregates, live model provenance), an
**enhanced
`/ready`** with an in-process statistics rollup + a soft `healthy` degradation
signal + a `/ready/stats` view, the latency breakdown **on the `anomaly.detected`
event**, and a **provisioned 12-panel Grafana dashboard** over a Prometheus that
scrapes the service every 5 s. No detection logic changed; every number is from a
real run. Regression held at parity throughout (1056 passing, 18 deselected).
