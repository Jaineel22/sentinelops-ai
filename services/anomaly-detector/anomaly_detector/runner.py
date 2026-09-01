"""The scrape -> score -> publish loop."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

import httpx
from ml.data.schema import SIGNAL_COLUMNS
from ml.inference import AnomalyResult, DetectorService

from anomaly_detector.config import Settings
from anomaly_detector.events import anomaly_event
from anomaly_detector.metrics import DetectorMetrics
from anomaly_detector.metrics_source import MetricsSource, ScrapeError, SignalWindow
from sentinelops_common.kafka import KafkaJsonProducer
from sentinelops_common.obs import get_tracer

logger = logging.getLogger("anomaly_detector.runner")


class DetectorRunner:
    def __init__(
        self,
        settings: Settings,
        *,
        detector: DetectorService,
        producer: KafkaJsonProducer,
        metrics: DetectorMetrics,
        client: httpx.AsyncClient,
    ) -> None:
        self._settings = settings
        self._detector = detector
        self._producer = producer
        self._metrics = metrics
        self._source = MetricsSource(settings.detector.target_metrics_url, client=client)
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.healthy = False
        self.scored = 0
        self.published = 0

    async def start(self) -> None:
        self.healthy = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
        self.healthy = False

    async def _run(self) -> None:
        interval = self._settings.detector.poll_interval_seconds
        try:
            while not self._stop.is_set():
                try:
                    await self.tick()
                except ScrapeError as exc:
                    self._metrics.scrapes.add(1, {"outcome": "error"})
                    logger.warning("scrape failed: %s", exc)
                except Exception:
                    logger.exception("unexpected error in detector tick")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=interval)
        finally:
            self.healthy = False

    async def tick(self) -> None:
        """One scrape/score/publish cycle. Separated out so tests can drive it."""

        window = await self._source.next_window()
        self._metrics.scrapes.add(1, {"outcome": "ok"})
        if window is None:
            return

        tracer = get_tracer()
        started = time.perf_counter()
        with tracer.start_as_current_span("detector.score_window") as span:
            record: dict[str, object] = {c: window.signals[c] for c in SIGNAL_COLUMNS}
            record["window_start"] = window.window_start.isoformat()
            record["window_end"] = window.window_end.isoformat()
            result = self._detector.score_window(record)
            self.scored += 1
            self._metrics.windows_scored.add(1)
            self._metrics.score_duration.record(time.perf_counter() - started)
            span.set_attribute("detector.is_anomaly", result.is_anomaly)
            span.set_attribute("detector.score", result.score)

            if result.is_anomaly or not self._settings.detector.publish_only_anomalies:
                await self._publish(window, result)

    async def _publish(self, window: SignalWindow, result: AnomalyResult) -> None:
        envelope = anomaly_event(
            window,
            result,
            service=self._settings.detector.target_service,
            environment=self._settings.detector.environment,
        )
        try:
            await self._producer.publish(
                self._settings.kafka.anomaly_topic,
                envelope,
                key=self._settings.detector.target_service,
            )
        except Exception:
            self._metrics.publish_failures.add(1)
            logger.exception("failed to publish anomaly.detected")
            return
        self.published += 1
        self._metrics.anomalies_published.add(1)
        logger.info(
            "anomaly published",
            extra={
                "event_id": envelope.event_id,
                "score": result.score,
                "threshold": result.threshold,
                "abnormal_signals": envelope.payload["abnormal_signals"],
            },
        )
