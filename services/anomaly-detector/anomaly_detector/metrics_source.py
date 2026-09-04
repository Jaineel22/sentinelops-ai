"""Scrape ``orders-service`` ``/metrics`` and turn consecutive scrapes into
per-window operational signals.

Reuses the Phase 2 offline pipeline verbatim (:func:`ml.data.prepare.window_signals`
over :class:`~ml.data.prometheus_parse.MetricSnapshot`) so a live window and a
collected training window are computed identically.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from ml.data.prepare import window_signals
from ml.data.prometheus_parse import MetricSnapshot, parse_metrics

logger = logging.getLogger("anomaly_detector.metrics_source")


class ScrapeError(RuntimeError):
    """The target ``/metrics`` endpoint could not be scraped or parsed."""


@dataclass(frozen=True)
class SignalWindow:
    window_start: datetime
    window_end: datetime
    dt_seconds: float
    signals: dict[str, float]
    # Phase 7B: wall-clock instant the scrape that produced this window finished.
    # Defaults to "now" so windows built in tests need not supply it.
    scrape_time: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


class MetricsSource:
    """Holds the previous scrape so each new one yields a closed window."""

    def __init__(self, url: str, *, client: httpx.AsyncClient) -> None:
        self._url = url
        self._client = client
        self._prev: MetricSnapshot | None = None
        self._prev_at: float | None = None
        self._prev_wall: datetime | None = None

    async def _scrape(self) -> MetricSnapshot:
        try:
            resp = await self._client.get(self._url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ScrapeError(f"GET {self._url} failed: {exc}") from exc
        try:
            return parse_metrics(resp.text)
        except Exception as exc:  # surface any parse failure uniformly
            raise ScrapeError(f"could not parse metrics from {self._url}: {exc}") from exc

    async def next_window(self) -> SignalWindow | None:
        """Scrape once. Returns ``None`` for the first scrape (no prior point) or
        an invalid window (counter reset / zero elapsed time)."""

        now = time.monotonic()
        wall = datetime.now(tz=UTC)
        snapshot = await self._scrape()
        scraped_at = datetime.now(tz=UTC)  # after the scrape I/O completes

        prev, prev_at, prev_wall = self._prev, self._prev_at, self._prev_wall
        self._prev, self._prev_at, self._prev_wall = snapshot, now, wall

        if prev is None or prev_at is None or prev_wall is None:
            return None

        dt = now - prev_at
        signals = window_signals(prev, snapshot, dt)
        if signals is None:
            logger.warning("skipping invalid telemetry window (dt=%.3fs)", dt)
            return None
        return SignalWindow(
            window_start=prev_wall,
            window_end=wall,
            dt_seconds=dt,
            signals=signals,
            scrape_time=scraped_at,
        )
