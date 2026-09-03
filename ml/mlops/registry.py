"""MLflow Model Registry access for the SentinelOps anomaly detector (Phase 6B).

A trained detector is a joblib bundle (see ``ml.models.base``). Sub-phase 6A
logs that bundle as an artifact under a run's ``model/`` path; here we register
it as an **MLflow model version** and manage promotion **aliases**:

* ``candidate``          — the most recently registered version, awaiting the gate
* ``champion``           — the version inference should serve (resolved in 6C)
* ``previous-champion``  — the immediate predecessor, kept for rollback

Deprecated MLflow *stages* (``Staging`` / ``Production``) are never used
([ADR-032](../../docs/decisions/adr-032-alias-strategy.md)). The registry
manages *versions and aliases* — it is not an execution mechanism, and a model
version's source is always a bundle produced by this project's own training
pipeline ([ADR-031](../../docs/decisions/adr-031-mlflow-tracking-and-registry.md)).

``mlflow`` is imported lazily so ``import ml.mlops.registry`` works without the
``mlops`` extra; every function here raises a clear error if it is missing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ml.mlops.config import MLflowSettings, make_console_emoji_safe

if TYPE_CHECKING:
    from mlflow.entities.model_registry import ModelVersion
    from mlflow.tracking import MlflowClient

    from ml.monitoring.baseline import BaselineDistribution

logger = logging.getLogger("ml.mlops.registry")

CANDIDATE_ALIAS = "candidate"
CHAMPION_ALIAS = "champion"
PREVIOUS_CHAMPION_ALIAS = "previous-champion"

# Artifact sub-path the training pipeline (ml.mlops.tracking.log_run) logs the
# model bundle under. A model version's source is ``runs:/<run_id>/model``.
MODEL_ARTIFACT_PATH = "model"

# Phase 6D: the drift baseline lives next to the model on the version's source run.
BASELINE_ARTIFACT = "baseline/baseline.json"
BASELINE_TAG = "monitoring.baseline"


def _get_client(settings: MLflowSettings) -> MlflowClient:
    from mlflow.tracking import MlflowClient

    make_console_emoji_safe()
    return MlflowClient(tracking_uri=settings.tracking_uri)


def register_model(
    model_path: str,
    run_id: str,
    settings: MLflowSettings,
    *,
    description: str | None = None,
) -> str:
    """Register the model bundle logged under ``run_id`` as a new model version.

    ``model_path`` is the local bundle path (informational — recorded in the
    version description); the registered *source* is the run artifact URI
    ``runs:/<run_id>/model``, which also links the version back to its run for
    lineage. Returns the new version number as a string.
    """

    import mlflow

    make_console_emoji_safe()
    mlflow.set_tracking_uri(settings.tracking_uri)
    # Fail early + clearly if the run does not exist.
    _get_client(settings).get_run(run_id)

    source = f"runs:/{run_id}/{MODEL_ARTIFACT_PATH}"
    version = mlflow.register_model(model_uri=source, name=settings.registered_model_name)

    desc = description or f"bundle={model_path!r}; run={run_id}"
    _get_client(settings).update_model_version(
        name=settings.registered_model_name, version=version.version, description=desc
    )
    logger.info(
        "registered %s v%s from run %s", settings.registered_model_name, version.version, run_id
    )
    return str(version.version)


def resolve_alias(settings: MLflowSettings, alias: str | None = None) -> tuple[str, str, str]:
    """Resolve ``alias`` (default: ``settings.model_alias``) to
    ``(version, run_id, model_uri)``. ``model_uri`` (``models:/<name>@<alias>``)
    is what a consumer loads. Raises ``MlflowException`` if the alias is unset."""

    alias = alias or settings.model_alias
    version = _get_client(settings).get_model_version_by_alias(
        settings.registered_model_name, alias
    )
    model_uri = f"models:/{settings.registered_model_name}@{alias}"
    return str(version.version), str(version.run_id), model_uri


def set_alias(settings: MLflowSettings, version: str | int, alias: str) -> None:
    """Point ``alias`` at ``version`` (idempotent; a re-point just moves it)."""

    _get_client(settings).set_registered_model_alias(
        settings.registered_model_name, alias, str(version)
    )
    logger.info("alias %r -> %s v%s", alias, settings.registered_model_name, version)


def delete_alias(settings: MLflowSettings, alias: str) -> None:
    """Remove ``alias`` if it exists (used when there is no predecessor to keep)."""

    try:
        _get_client(settings).delete_registered_model_alias(settings.registered_model_name, alias)
    except Exception as exc:  # alias absent — nothing to remove
        logger.debug("no %r alias to delete (%s)", alias, exc)


def add_version_tag(settings: MLflowSettings, version: str | int, key: str, value: str) -> None:
    """Attach an immutable-ish audit tag to a model version (e.g. why it was promoted)."""

    _get_client(settings).set_model_version_tag(
        settings.registered_model_name, str(version), key, value
    )


def get_registered_aliases(settings: MLflowSettings) -> dict[str, list[str]]:
    """``{version: [alias, ...]}`` for the registered model (empty if unregistered).

    ``search_model_versions`` does not populate per-version aliases; the
    registered model's alias map is the single source for them.
    """

    try:
        model = _get_client(settings).get_registered_model(settings.registered_model_name)
    except Exception as exc:
        logger.debug("no registered model %r yet (%s)", settings.registered_model_name, exc)
        return {}
    by_version: dict[str, list[str]] = {}
    for alias, version in model.aliases.items():
        by_version.setdefault(str(version), []).append(alias)
    return by_version


def set_model_baseline(
    settings: MLflowSettings, version: str | int, baseline: BaselineDistribution
) -> None:
    """Store a drift baseline (Phase 6D) for a model version: log it to the
    version's source run at ``baseline/baseline.json`` and tag the version."""

    client = _get_client(settings)
    model_version = client.get_model_version(settings.registered_model_name, str(version))
    client.log_dict(model_version.run_id, baseline.to_dict(), BASELINE_ARTIFACT)
    client.set_model_version_tag(
        settings.registered_model_name, str(version), BASELINE_TAG, BASELINE_ARTIFACT
    )
    client.set_model_version_tag(
        settings.registered_model_name,
        str(version),
        "monitoring.baseline_n_samples",
        str(baseline.n_samples),
    )
    logger.info(
        "stored drift baseline for %s v%s (%d samples)",
        settings.registered_model_name,
        version,
        baseline.n_samples,
    )


def get_model_baseline(settings: MLflowSettings, version: str | int) -> BaselineDistribution | None:
    """Retrieve a model version's drift baseline, or ``None`` if none was stored /
    it cannot be downloaded."""

    import json

    from mlflow.artifacts import download_artifacts

    from ml.monitoring.baseline import BaselineDistribution

    try:
        model_version = _get_client(settings).get_model_version(
            settings.registered_model_name, str(version)
        )
        local = download_artifacts(
            run_id=model_version.run_id,
            artifact_path=BASELINE_ARTIFACT,
            tracking_uri=settings.tracking_uri,
        )
    except Exception as exc:
        logger.debug("no drift baseline for v%s (%s)", version, exc)
        return None
    payload = json.loads(Path(local).read_text(encoding="utf-8"))
    return BaselineDistribution.from_dict(payload)


def get_champion_metrics(settings: MLflowSettings) -> dict[str, float] | None:
    """The champion version's evaluation metrics (from its source run), or
    ``None`` if no champion alias is set / the registry is unreachable."""

    try:
        _version, run_id, _uri = resolve_alias(settings, CHAMPION_ALIAS)
        run = _get_client(settings).get_run(run_id)
    except Exception as exc:
        logger.debug("no champion metrics available (%s)", exc)
        return None
    return dict(run.data.metrics)


def get_model_lineage(settings: MLflowSettings, version: str | int) -> dict[str, Any]:
    """Everything needed to trace a version back to what produced it: run id,
    params, metrics, tags, aliases, artifact URIs."""

    client = _get_client(settings)
    model_version = client.get_model_version(settings.registered_model_name, str(version))
    run = client.get_run(model_version.run_id)
    return {
        "name": settings.registered_model_name,
        "version": str(model_version.version),
        "run_id": model_version.run_id,
        "source": model_version.source,
        "aliases": sorted(model_version.aliases),
        "description": model_version.description or "",
        "creation_timestamp": model_version.creation_timestamp,
        "params": dict(run.data.params),
        "metrics": dict(run.data.metrics),
        "tags": {k: v for k, v in run.data.tags.items() if not k.startswith("mlflow.")},
        "run_artifact_uri": run.info.artifact_uri,
    }


def list_model_versions(settings: MLflowSettings) -> list[ModelVersion]:
    """All versions of the registered model (empty list if it is not registered)."""

    try:
        return list(
            _get_client(settings).search_model_versions(
                f"name = '{settings.registered_model_name}'"
            )
        )
    except Exception as exc:
        logger.debug("no registered model %r yet (%s)", settings.registered_model_name, exc)
        return []
