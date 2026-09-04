"""OpenTelemetry instruments for the anomaly-detector (low-cardinality labels).

ADR-007: metrics are OTel instruments, exported for a Prometheus scrape at
``GET /metrics`` via the OpenTelemetry Prometheus exporter. Phase 7A adds the
*inference* view on top of the existing scrape/publish counters: how many
windows the model scored, how long a score took, how many came back anomalous,
the score distribution, and which model version/type is live. Phase 7B breaks
the detection latency into its pipeline hops (window age at scrape, scrape ->
publish, end to end).

Labels stay low-cardinality: ``model_version`` only (the service name is a
resource attribute, set once by :func:`sentinelops_common.obs.configure_observability`).
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache

from opentelemetry.metrics import CallbackOptions, Counter, Histogram, Meter, Observation

from sentinelops_common.obs import get_meter

# `model_type` is a string on the model bundle; a Prometheus gauge needs a
# number, so map the known types to small integer codes (0 = anything else).
_MODEL_TYPE_CODES: dict[str, int] = {"isolation_forest": 1, "random_forest": 2}

# Set on `detector.model.version` when the version string is not numeric. MLflow
# registry versions are integers-as-strings ("3"); the local dev bundle carries a
# semver `ml_version` ("0.2.0") that has no meaningful float form -- read the
# `model_version` label on `detector.model.info` in that case.
_VERSION_NOT_NUMERIC = -1.0


def _version_as_float(version: str) -> float:
    try:
        return float(version)
    except (TypeError, ValueError):
        return _VERSION_NOT_NUMERIC


class DetectorMetrics:
    def __init__(self, meter: Meter | None = None) -> None:
        meter = meter or get_meter()
        # --- scrape / publish loop (Phase 3) ---------------------------------
        self.scrapes: Counter = meter.create_counter(
            "detector.scrapes", unit="1", description="Target /metrics scrapes, by outcome."
        )
        self.windows_scored: Counter = meter.create_counter(
            "detector.windows.scored", unit="1", description="Telemetry windows scored."
        )
        self.anomalies_published: Counter = meter.create_counter(
            "detector.anomalies.published",
            unit="1",
            description="anomaly.detected events published.",
        )
        self.publish_failures: Counter = meter.create_counter(
            "detector.publish.failures", unit="1", description="Failed anomaly.detected publishes."
        )
        self.score_duration: Histogram = meter.create_histogram(
            "detector.score.duration", unit="s", description="Time to score one window."
        )
        # --- inference view (Phase 7A) -------------------------------------
        self.inference_requests: Counter = meter.create_counter(
            "detector.inference.requests",
            unit="1",
            description="Model inferences performed (one per scored window).",
        )
        # Scoring one window is a sub-millisecond-to-tens-of-milliseconds op;
        # the OTel default buckets (whole seconds) would collapse it into one
        # bucket and make `histogram_quantile` meaningless (Phase 7D).
        self.inference_duration: Histogram = meter.create_histogram(
            "detector.inference.duration",
            unit="s",
            description="Model scoring latency for one window.",
            explicit_bucket_boundaries_advisory=(
                0.0005,
                0.001,
                0.0025,
                0.005,
                0.01,
                0.025,
                0.05,
                0.1,
                0.25,
                0.5,
                1.0,
            ),
        )
        self.anomalies_detected: Counter = meter.create_counter(
            "detector.anomalies.detected",
            unit="1",
            description="Windows the model scored as anomalous.",
        )
        # Anomaly scores sit around the calibrated threshold (~0.5), higher =
        # more anomalous; even 0..1 buckets so the distribution panel is legible.
        self.anomaly_score: Histogram = meter.create_histogram(
            "detector.anomaly.score",
            unit="1",
            description="Distribution of raw anomaly scores.",
            explicit_bucket_boundaries_advisory=(
                0.1,
                0.2,
                0.3,
                0.4,
                0.5,
                0.6,
                0.7,
                0.8,
                0.9,
                1.0,
            ),
        )
        self.detection_latency: Histogram = meter.create_histogram(
            "detector.detection.latency",
            unit="s",
            description="Wall time from window close to anomaly.detected publish.",
        )
        # --- detection latency breakdown (Phase 7B) -----------------------
        # Sub-second buckets: these are pipeline hops, not model training.
        _timing_buckets = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
        self.window_age_at_scrape: Histogram = meter.create_histogram(
            "detector.window.age_at_scrape",
            unit="s",
            description="Time between a window closing and the scrape that carried it.",
            explicit_bucket_boundaries_advisory=_timing_buckets,
        )
        self.scrape_to_publish: Histogram = meter.create_histogram(
            "detector.scrape.to.publish",
            unit="s",
            description="Time from scrape to anomaly publish (published anomalies only).",
            explicit_bucket_boundaries_advisory=_timing_buckets,
        )
        self.end_to_end_detection_latency: Histogram = meter.create_histogram(
            "detector.detection.latency.end_to_end",
            unit="s",
            description="Full time from window close to anomaly publish.",
            explicit_bucket_boundaries_advisory=_timing_buckets,
        )
        # Model provenance is *current state*, not an event stream, so these are
        # observable gauges: their callbacks are polled on every collection, so
        # the series never goes stale in Prometheus between model changes. (A
        # synchronous gauge is only re-exported on the collection right after a
        # `set`, which Prometheus then marks stale -- Phase 7D.)
        self._model_version_value = _VERSION_NOT_NUMERIC
        self._model_type_value = 0
        self._model_info_attrs: dict[str, str] = {
            "model_version": "unknown",
            "model_type": "unknown",
        }
        meter.create_observable_gauge(
            "detector.model.version",
            callbacks=[self._observe_model_version],
            unit="1",
            description="Active model version (numeric; -1 when the version is not numeric).",
        )
        meter.create_observable_gauge(
            "detector.model.type",
            callbacks=[self._observe_model_type],
            unit="1",
            description="Active model type (1=isolation_forest, 2=random_forest, 0=other).",
        )
        meter.create_observable_gauge(
            "detector.model.info",
            callbacks=[self._observe_model_info],
            unit="1",
            description="Always 1; carries the model_version / model_type labels.",
        )
        # --- service-level aggregates (Phase 7C) -------------------------
        # Unlabelled twins of the inference view, for "this instance, all
        # models" panels that must not fan out on model_version.
        self.service_inference_requests: Counter = meter.create_counter(
            "detector.service.inference.requests",
            unit="1",
            description="Total inference requests (service-level, no model_version).",
        )
        self.service_anomalies_detected: Counter = meter.create_counter(
            "detector.service.anomalies.detected",
            unit="1",
            description="Total anomalies detected (service-level, no model_version).",
        )
        self.service_inference_latency: Histogram = meter.create_histogram(
            "detector.service.inference.latency",
            unit="s",
            description="Inference latency (service-level, no model_version).",
        )

    # --- recording helpers ------------------------------------------------
    def record_inference(
        self,
        *,
        model_version: str,
        latency_seconds: float,
        is_anomaly: bool,
        score: float,
    ) -> None:
        """Record one model inference: the request, its latency, its score, and
        (when anomalous) the anomaly counter."""

        attrs = {"model_version": model_version}
        self.inference_requests.add(1, attrs)
        self.inference_duration.record(latency_seconds, attrs)
        self.anomaly_score.record(score, attrs)
        if is_anomaly:
            self.anomalies_detected.add(1, attrs)

    def record_service_inference(self, latency_seconds: float, is_anomaly: bool) -> None:
        """Service-level aggregate: one inference, its latency, and (if anomalous)
        the anomaly counter -- all without a ``model_version`` label."""

        self.service_inference_requests.add(1)
        self.service_inference_latency.record(latency_seconds)
        if is_anomaly:
            self.service_anomalies_detected.add(1)

    def record_detection_latency(self, latency_seconds: float) -> None:
        """Record wall time from a window closing to its anomaly being published."""

        self.detection_latency.record(latency_seconds)

    def record_window_age_at_scrape(self, age_seconds: float) -> None:
        self.window_age_at_scrape.record(age_seconds)

    def record_scrape_to_publish(self, latency_seconds: float) -> None:
        self.scrape_to_publish.record(latency_seconds)

    def record_end_to_end_latency(self, latency_seconds: float) -> None:
        self.end_to_end_detection_latency.record(latency_seconds)

    def set_model_info(self, *, version: str, model_type: str) -> None:
        """Update the live model's version / type (call at startup and whenever
        the model is swapped). The observable gauges pick this up on their next
        collection."""

        self._model_version_value = _version_as_float(version)
        self._model_type_value = _MODEL_TYPE_CODES.get(model_type, 0)
        self._model_info_attrs = {"model_version": version, "model_type": model_type}

    def _observe_model_version(self, _options: CallbackOptions) -> Iterable[Observation]:
        return [Observation(self._model_version_value)]

    def _observe_model_type(self, _options: CallbackOptions) -> Iterable[Observation]:
        return [Observation(self._model_type_value)]

    def _observe_model_info(self, _options: CallbackOptions) -> Iterable[Observation]:
        return [Observation(1, dict(self._model_info_attrs))]


@lru_cache
def get_metrics() -> DetectorMetrics:
    return DetectorMetrics()
