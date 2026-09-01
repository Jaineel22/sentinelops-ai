"""Get a scoring-ready :class:`~ml.inference.DetectorService`.

The model is the Phase 2 primary detector (Isolation Forest) trained on the
committed ``run_a`` telemetry with a fixed seed, so every environment that
starts from an empty cache produces the *same* model. If the cache file already
exists it is loaded as-is.

This deliberately reuses the Phase 2 training code path (same split, same
feature builder, same calibration) — the streaming detector must not diverge
from what was evaluated in ``docs/architecture/phase-2.md``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ml.config import CANONICAL_RUN_MAIN, set_global_seed
from ml.data.prepare import load_processed_run
from ml.data.schema import FEATURE_COLUMNS
from ml.features.engineering import build_features
from ml.inference import DetectorService
from ml.models import IsolationForestDetector
from ml.splits import chronological_split

logger = logging.getLogger("anomaly_detector.training")

# Same chronological split fractions as ``ml.experiments.catalog._track_a_chrono``.
_VAL_FRACTION = 0.17
_TEST_FRACTION = 0.33


def ensure_detector(model_path: str | Path, *, seed: int = 42) -> DetectorService:
    path = Path(model_path)
    if path.exists():
        logger.info("loading cached detector model", extra={"model_path": str(path)})
        return DetectorService.load(path)

    logger.info(
        "no cached model; training Isolation Forest from %s (seed=%s)", CANONICAL_RUN_MAIN, seed
    )
    detector = _train(seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    detector.save(path)
    logger.info("wrote detector model", extra={"model_path": str(path)})
    return DetectorService(detector)


def _train(seed: int) -> IsolationForestDetector:
    set_global_seed(seed)
    feats = build_features(load_processed_run(CANONICAL_RUN_MAIN))
    split = chronological_split(feats, val_fraction=_VAL_FRACTION, test_fraction=_TEST_FRACTION)
    detector = IsolationForestDetector(
        feature_names=list(FEATURE_COLUMNS), random_state=seed, n_estimators=200
    )
    detector.fit(
        split.train,
        split.train["is_anomaly"].astype(int),
        random_seed=seed,
        training_dataset=f"sentinelops/{CANONICAL_RUN_MAIN}",
    )
    detector.calibrate_threshold(split.val, split.val["is_anomaly"].astype(int), objective="f1")
    return detector
