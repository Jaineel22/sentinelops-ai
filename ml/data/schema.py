"""Schema definitions shared across the pipeline.

Three column groups:

* **signal columns** — per-window operational measurements produced by
  :mod:`ml.data.prepare` from raw metric scrapes.
* **feature columns** — the model input, produced by
  :mod:`ml.features.engineering` (signals + rolling/lag/derivative features).
  This is an explicit allow-list.
* **metadata / label columns** — never fed to a model.

``FORBIDDEN_FEATURE_SUBSTRINGS`` names things that would leak the answer
(the injected-fault ground truth). ``tests/ml/test_features.py`` asserts no
feature column matches them.
"""

from __future__ import annotations

# --- window identity / bookkeeping -------------------------------------------
META_COLUMNS: tuple[str, ...] = ("run_id", "window_start", "window_end", "window_seconds")

# --- ground truth (kept in a separate frame / never a feature) --------------
LABEL_COLUMNS: tuple[str, ...] = ("label", "is_anomaly")

# --- per-window operational signals ----------------------------------------
SIGNAL_COLUMNS: tuple[str, ...] = (
    "request_rate",  # successful+failed POST /orders per second
    "error_rate",  # 5xx / total requests in the window
    "success_rate",  # 2xx / total requests
    "latency_mean_ms",  # mean POST /orders server latency
    "latency_p50_ms",
    "latency_p90_ms",
    "latency_p95_ms",
    "publish_rate",  # Kafka publish attempts per second
    "publish_error_rate",  # failed publishes / attempts
    "publish_latency_mean_ms",  # mean publish latency (percentiles unavailable — see phase-2.md)
    "orders_created_rate",  # orders successfully created per second
)

# --- the model input: signals + engineered features (see engineering.py) ----
DERIVED_FEATURE_COLUMNS: tuple[str, ...] = (
    "request_rate_roll_mean",
    "request_rate_roll_std",
    "request_rate_delta",
    "error_rate_roll_mean",
    "error_rate_delta",
    "latency_mean_ms_roll_mean",
    "latency_mean_ms_roll_std",
    "latency_mean_ms_delta",
    "latency_p95_ms_roll_mean",
    "publish_error_rate_roll_mean",
    "publish_latency_mean_ms_roll_mean",
    "traffic_growth_rate",  # (request_rate - lag1) / lag1
)

FEATURE_COLUMNS: tuple[str, ...] = SIGNAL_COLUMNS + DERIVED_FEATURE_COLUMNS

FORBIDDEN_FEATURE_SUBSTRINGS: tuple[str, ...] = (
    "label",
    "is_anomaly",
    "scenario",
    "inject",  # failure-injection counters / config
    "simulated",
    "debug",
    "run_id",
    "window_start",
    "window_end",
)

REQUIRED_SIGNAL_COLUMNS: tuple[str, ...] = META_COLUMNS + SIGNAL_COLUMNS
