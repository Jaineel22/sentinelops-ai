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
from ml.mlops.config import MLflowSettings
from ml.models import IsolationForestDetector
from ml.splits import chronological_split

logger = logging.getLogger("anomaly_detector.training")

# Same chronological split fractions as ``ml.experiments.catalog._track_a_chrono``.
_VAL_FRACTION = 0.17
_TEST_FRACTION = 0.33


def ensure_detector(
    model_path: str | Path,
    *,
    seed: int = 42,
    mlflow_settings: MLflowSettings | None = None,
) -> DetectorService:
    """Get a scoring-ready `DetectorService`.

    Phase 2/3 behaviour (unchanged): load the cached bundle at ``model_path``, or
    train it once from the committed ``run_a`` telemetry. Phase 6C: if
    ``mlflow_settings`` is given and has a ``tracking_uri``, resolve the model
    from the MLflow registry by alias instead — falling back to the local path
    only when ``mlflow_settings.required`` is ``False`` (logged explicitly).
    """

    if mlflow_settings is not None and mlflow_settings.tracking_uri:
        return ensure_detector_from_registry(model_path, mlflow_settings, seed=seed)
    return _ensure_local_detector(model_path, seed=seed)


def ensure_detector_from_registry(
    model_path: str | Path, mlflow_settings: MLflowSettings, *, seed: int = 42
) -> DetectorService:
    """Load the detector from the MLflow registry (alias resolution).

    On failure: re-raise when ``mlflow_settings.required`` is ``True`` (hard
    fail → ``/ready`` reports 503); otherwise log a warning and fall back to the
    local bundle, tagged ``source="local-fallback"`` so operators can see the
    registry was not used.
    """

    try:
        service = DetectorService.from_registry(mlflow_settings)
    except Exception as exc:
        if mlflow_settings.required:
            logger.error(
                "MLFLOW_REQUIRED=true but the model registry could not be used "
                "(%s: %s) - refusing to start with a non-registry model",
                type(exc).__name__,
                exc,
            )
            raise
        logger.warning(
            "MLflow registry unavailable (%s: %s); FALLING BACK to the local model "
            "at %s - this is NOT the registry champion (MLFLOW_REQUIRED=false)",
            type(exc).__name__,
            exc,
            model_path,
        )
        fallback = _ensure_local_detector(model_path, seed=seed)
        fallback._mark_source("local-fallback")
        return fallback

    logger.info(
        "loaded detector from MLflow registry: model=%s alias=%s version=%s",
        mlflow_settings.registered_model_name,
        mlflow_settings.model_alias,
        service.model_version,
    )
    return service


def get_detector_source(detector: DetectorService) -> dict[str, str]:
    """Provenance summary for the `/ready` and `/model-info` endpoints."""

    return {
        "source": detector.source,
        "model_version": detector.model_version,
        "model_type": detector.model_type,
    }


def _ensure_local_detector(model_path: str | Path, *, seed: int = 42) -> DetectorService:
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
