"""Detection-latency timeline for one scrape -> score -> publish cycle (Phase 7B).

Phase 7A measured one number: window close -> anomaly publish. Phase 7B keeps a
small timeline of Unix timestamps around the cycle so the end-to-end latency can
be broken into its parts (how stale the window was when scraped, how long
scoring took, scrape -> publish) and the same breakdown can ride along on the
``anomaly.detected`` event for downstream debugging.

All timestamps are ``time.time()`` / ``datetime.timestamp()`` seconds since the
epoch (UTC) so differences are timezone-independent. ``perf_counter`` stays the
source for the high-precision *inference duration* metric in
:mod:`anomaly_detector.metrics`; the value here is a coarser wall-clock echo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anomaly_detector.metrics import DetectorMetrics


@dataclass(frozen=True)
class TimingPoint:
    """A single named instant in a detection cycle."""

    name: str
    timestamp: float
    service: str
    window_start: str
    window_end: str


@dataclass(frozen=True)
class DetectionTimeline:
    """The instants captured across one window's scrape -> score -> publish cycle.

    ``publish_time`` is ``None`` for a window that was scored but not published
    (a normal window, or a publish failure).
    """

    scrape_time: float
    window_close_time: float
    inference_start_time: float
    inference_end_time: float
    service: str
    is_anomaly: bool
    publish_time: float | None = None


def calculate_latencies(timeline: DetectionTimeline) -> dict[str, float]:
    """Derive the latency breakdown (all in seconds) from a timeline.

    The publish-dependent entries (``scrape_to_publish``, ``window_to_publish``,
    ``total_detection_latency``) are present only for a published anomaly.
    """

    latencies = {
        "window_age_at_scrape": timeline.scrape_time - timeline.window_close_time,
        "inference_duration": timeline.inference_end_time - timeline.inference_start_time,
    }
    if timeline.is_anomaly and timeline.publish_time is not None:
        latencies["scrape_to_publish"] = timeline.publish_time - timeline.scrape_time
        latencies["window_to_publish"] = timeline.publish_time - timeline.window_close_time
        latencies["total_detection_latency"] = timeline.publish_time - timeline.window_close_time
    return latencies


def record_detection_timeline(timeline: DetectionTimeline, metrics: DetectorMetrics) -> None:
    """Record every derivable latency from ``timeline`` to the OTel instruments."""

    latencies = calculate_latencies(timeline)
    metrics.record_window_age_at_scrape(latencies["window_age_at_scrape"])
    if "scrape_to_publish" in latencies:
        metrics.record_scrape_to_publish(latencies["scrape_to_publish"])
    if "total_detection_latency" in latencies:
        metrics.record_end_to_end_latency(latencies["total_detection_latency"])
