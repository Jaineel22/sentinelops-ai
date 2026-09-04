"""Phase 7D - the monitoring stack config is well-formed and internally consistent.

These are static checks (valid JSON/YAML, expected panels, matching data-source
uids, queries against metrics the service actually exposes). Bringing Grafana up
and rendering the panels is a Compose-level check, not a unit test.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_MON = _ROOT / "infrastructure" / "monitoring"
_DASHBOARD = _MON / "grafana" / "dashboards" / "anomaly-detector.json"
_DATASOURCE = _MON / "grafana" / "provisioning" / "datasources" / "prometheus.yaml"
_DASH_PROVISION = _MON / "grafana" / "provisioning" / "dashboards" / "dashboards.yaml"
_PROM_CONFIG = _MON / "prometheus" / "prometheus.yml"

# Prometheus series the anomaly-detector actually exposes (Phases 7A-7C), used to
# sanity-check the dashboard queries. Kept in sync with
# services/anomaly-detector/anomaly_detector/metrics.py.
_KNOWN_METRIC_PREFIXES = (
    "detector_inference_requests_total",
    "detector_anomalies_detected_total",
    "detector_inference_duration_seconds",
    "detector_anomaly_score",
    "detector_detection_latency_end_to_end_seconds",
    "detector_model_info",
    "process_start_time_seconds",
)


@pytest.fixture(scope="module")
def dashboard() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_DASHBOARD.read_text(encoding="utf-8"))
    return data


def _panel_targets(panel: dict[str, Any]) -> list[str]:
    return [t["expr"] for t in panel.get("targets", []) if "expr" in t]


def test_dashboard_json_valid(dashboard: dict[str, Any]) -> None:
    assert dashboard["uid"] == "anomaly-detector"
    assert dashboard["title"] == "Anomaly Detector - Inference & Performance"
    assert set(dashboard["tags"]) >= {"sentinelops", "anomaly-detector", "mlops"}
    assert dashboard["time"] == {"from": "now-15m", "to": "now"}
    assert dashboard["refresh"]


def test_dashboard_has_required_panels(dashboard: dict[str, Any]) -> None:
    panels = dashboard["panels"]
    assert len(panels) >= 12

    titles = {p["title"] for p in panels}
    for expected in (
        "Inference Requests / sec",
        "Anomalies Detected / sec",
        "Inference Latency (p50, p95, p99)",
        "End-to-End Detection Latency (p95)",
        "Anomaly Score Distribution",
        "Current Model Version",
        "Current Model Type",
        "Total Inferences",
        "Total Anomalies",
        "Anomaly Rate",
        "Service Uptime",
        "Latest Inference Latency",
    ):
        assert expected in titles, f"missing panel: {expected}"

    types = {p["type"] for p in panels}
    assert {"timeseries", "stat", "gauge", "heatmap"} <= types

    # every panel has a unique id and at least one target with an expression
    ids = [p["id"] for p in panels]
    assert len(ids) == len(set(ids))
    for panel in panels:
        assert _panel_targets(panel), f"panel {panel['title']} has no query"


def test_dashboard_data_source(dashboard: dict[str, Any]) -> None:
    ds_doc = yaml.safe_load(_DATASOURCE.read_text(encoding="utf-8"))
    (ds,) = ds_doc["datasources"]
    assert ds["type"] == "prometheus"
    assert ds["uid"] == "prometheus"
    assert ds["url"] == "http://prometheus:9090"

    # every panel (and every target) points at that same data source uid
    for panel in dashboard["panels"]:
        assert panel["datasource"] == {"type": "prometheus", "uid": "prometheus"}
        for t in panel["targets"]:
            assert t["datasource"] == {"type": "prometheus", "uid": "prometheus"}


def test_dashboard_queries_use_known_metrics(dashboard: dict[str, Any]) -> None:
    for panel in dashboard["panels"]:
        for expr in _panel_targets(panel):
            assert any(m in expr for m in _KNOWN_METRIC_PREFIXES), (
                f"panel {panel['title']!r} query references an unknown metric: {expr}"
            )


def test_prometheus_config_valid() -> None:
    cfg = yaml.safe_load(_PROM_CONFIG.read_text(encoding="utf-8"))

    assert cfg["global"]["scrape_interval"]
    jobs = {j["job_name"] for j in cfg["scrape_configs"]}
    assert "anomaly-detector" in jobs

    detector_job = next(j for j in cfg["scrape_configs"] if j["job_name"] == "anomaly-detector")
    targets = detector_job["static_configs"][0]["targets"]
    assert targets == ["anomaly-detector:8000"]
    assert detector_job["metrics_path"] == "/metrics"


def test_grafana_provisioning_valid() -> None:
    ds = yaml.safe_load(_DATASOURCE.read_text(encoding="utf-8"))
    assert ds["apiVersion"] == 1
    assert ds["datasources"][0]["name"] == "Prometheus"

    prov = yaml.safe_load(_DASH_PROVISION.read_text(encoding="utf-8"))
    assert prov["apiVersion"] == 1
    (provider,) = prov["providers"]
    assert provider["type"] == "file"
    assert provider["options"]["path"] == "/etc/dashboards"


def test_compose_wires_the_monitoring_stack() -> None:
    compose = yaml.safe_load((_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert "prometheus" in services
    assert "grafana" in services

    prom_mounts = " ".join(services["prometheus"]["volumes"])
    assert "infrastructure/monitoring/prometheus/prometheus.yml" in prom_mounts

    graf_mounts = " ".join(services["grafana"]["volumes"])
    assert "infrastructure/monitoring/grafana/provisioning" in graf_mounts
    assert "infrastructure/monitoring/grafana/dashboards" in graf_mounts

    for vol in ("prometheus-data", "grafana-data"):
        assert vol in compose["volumes"]
