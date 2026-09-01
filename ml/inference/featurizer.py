"""Streaming feature construction for real-time scoring.

The live path receives one signal record per telemetry window. Engineered
features (rolling mean/std, deltas, growth rate) need the last few windows, so
:class:`StreamFeaturizer` keeps a small trailing buffer and reuses the *exact*
batch feature code (:func:`ml.features.build_features`) on it — guaranteeing the
live features equal the training features for the same input sequence
(asserted by ``tests/ml/test_features.py``).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from typing import Any

import pandas as pd

from ml.data.schema import SIGNAL_COLUMNS
from ml.features.engineering import FeatureConfig, build_features


class StreamFeaturizer:
    def __init__(self, config: FeatureConfig | None = None) -> None:
        self.config = config or FeatureConfig()
        # Need `rolling_window` rows for a fully-populated feature row.
        self._buffer: deque[dict[str, Any]] = deque(maxlen=self.config.rolling_window)

    def reset(self) -> None:
        self._buffer.clear()

    def push(self, signal_record: Mapping[str, Any]) -> pd.DataFrame:
        """Add a window's signals; return the 1-row engineered feature frame for
        that window (features consistent with batch training)."""

        missing = [c for c in SIGNAL_COLUMNS if c not in signal_record]
        if missing:
            raise ValueError(f"signal record missing columns: {missing}")

        row = dict(signal_record)
        row.setdefault("run_id", "stream")
        row.setdefault("window_start", pd.Timestamp.now(tz="UTC").isoformat())
        row.setdefault("window_end", row["window_start"])
        row.setdefault("window_seconds", 0.0)
        self._buffer.append(row)

        frame = pd.DataFrame(list(self._buffer))
        features = build_features(frame, self.config)
        return features.iloc[[-1]].reset_index(drop=True)
