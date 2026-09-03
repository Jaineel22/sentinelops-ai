"""MLflow experiment tracking for the Phase 2 training pipeline (Sub-phase 6A).

:func:`log_run` records **one MLflow run per trained detector**: the
experiment/model parameters, a reproducibility lineage block (git SHA, Python and
key package versions), every numeric metric produced by :mod:`ml.evaluation`, and
the generated artifacts (the experiment's report directory, the model bundle, and
the experiment spec as JSON).

All tracking is **best-effort and additive**. A missing ``mlflow`` install or an
unreachable tracking store logs a warning and returns ``None``; the caller's
training pipeline is never interrupted. Only real evaluation output is logged —
no metric is synthesised here.
"""

from __future__ import annotations

import json
import logging
import platform
import subprocess
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ml.mlops.config import MLflowSettings, make_console_emoji_safe

if TYPE_CHECKING:
    from ml.monitoring.baseline import BaselineDistribution

logger = logging.getLogger("ml.mlops.tracking")

# Artifact path (within a run) for the frozen drift baseline — the registry
# resolves it via the model version's source run (ml.mlops.registry).
BASELINE_ARTIFACT_PATH = "baseline"
BASELINE_ARTIFACT_FILE = "baseline.json"

# Recorded on every run so a model version can be traced back to the exact
# library stack that produced it.
_TRACKED_PACKAGES = ("mlflow", "mlflow-skinny", "scikit-learn", "numpy", "pandas", "scipy")


def get_git_sha() -> str:
    """Full ``HEAD`` commit SHA, or ``"unknown"`` if git is unavailable."""

    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        return sha or "unknown"
    except Exception:
        return "unknown"


def get_package_versions() -> dict[str, str]:
    """Installed versions of the ML/tracking libraries that shape a training run.

    Packages that are not installed are omitted rather than reported as a
    sentinel — the presence/absence is itself informative (e.g. ``mlflow`` vs
    ``mlflow-skinny``)."""

    versions: dict[str, str] = {}
    for name in _TRACKED_PACKAGES:
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            continue
    return versions


def setup_experiment(settings: MLflowSettings) -> str:
    """Point MLflow at ``settings.tracking_uri``, ensure the experiment exists,
    and return its id.

    Raises if ``mlflow`` is not installed or the tracking store is unreachable.
    Callers that must stay fail-safe should go through :func:`log_run`, which
    swallows those errors.
    """

    import mlflow

    mlflow.set_tracking_uri(settings.tracking_uri)
    experiment = mlflow.get_experiment_by_name(settings.experiment_name)
    experiment_id = (
        experiment.experiment_id
        if experiment is not None
        else mlflow.create_experiment(settings.experiment_name)
    )
    mlflow.set_experiment(experiment_id=experiment_id)
    return str(experiment_id)


def _flatten(prefix: str, value: Any, out: dict[str, Any]) -> None:
    if isinstance(value, Mapping):
        for key, val in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            _flatten(child, val, out)
    else:
        out[prefix] = value


def _numeric_metrics(metrics: Mapping[str, Any]) -> dict[str, float]:
    flat: dict[str, Any] = {}
    _flatten("", dict(metrics), flat)
    numeric: dict[str, float] = {}
    for key, val in flat.items():
        if val is None or isinstance(val, bool):
            continue
        if isinstance(val, (int, float)):
            numeric[key] = float(val)
    return numeric


def _stringify(value: Any) -> str:
    if value is None or isinstance(value, (str, int, float, bool)):
        return str(value)
    return json.dumps(value, sort_keys=True, default=str)


def log_run(
    experiment_spec: Mapping[str, Any],
    metrics: Mapping[str, Any],
    artifacts_path: str | Path | None,
    model_path: str | Path | None,
    settings: MLflowSettings,
    *,
    extra_params: Mapping[str, Any] | None = None,
    baseline: BaselineDistribution | None = None,
) -> str | None:
    """Log one training run to MLflow. Returns the run id, or ``None`` if MLflow
    is unavailable / the store is unreachable (a warning is logged, nothing
    raised).

    ``baseline`` (Phase 6D) is the frozen reference feature distribution; it is
    stored at ``baseline/baseline.json`` so a model version registered from this
    run carries its drift baseline.
    """

    try:
        import mlflow

        make_console_emoji_safe()
        setup_experiment(settings)
        run_name = str(
            experiment_spec.get("run_name") or experiment_spec.get("experiment") or "run"
        )
        with mlflow.start_run(run_name=run_name) as run:
            params: dict[str, Any] = {}
            _flatten("", dict(experiment_spec), params)
            if extra_params:
                _flatten("", dict(extra_params), params)
            params["lineage.git_sha"] = get_git_sha()
            params["lineage.python_version"] = platform.python_version()
            for pkg, ver in get_package_versions().items():
                params[f"lineage.pkg.{pkg}"] = ver
            mlflow.log_params({key: _stringify(val) for key, val in params.items()})

            numeric = _numeric_metrics(metrics)
            if numeric:
                mlflow.log_metrics(numeric)

            mlflow.log_dict(dict(experiment_spec), "experiment_spec.json")

            if artifacts_path is not None:
                report = Path(artifacts_path)
                if report.is_dir():
                    for item in sorted(report.iterdir()):
                        if item.is_file():
                            mlflow.log_artifact(str(item), artifact_path="report")
                elif report.is_file():
                    mlflow.log_artifact(str(report), artifact_path="report")

            if model_path is not None and Path(model_path).is_file():
                mlflow.log_artifact(str(model_path), artifact_path="model")

            if baseline is not None:
                mlflow.log_dict(
                    baseline.to_dict(), f"{BASELINE_ARTIFACT_PATH}/{BASELINE_ARTIFACT_FILE}"
                )

            return str(run.info.run_id)
    except ImportError:
        logger.warning("mlflow not installed - skipping experiment tracking")
        return None
    except Exception as exc:
        # Tracking is best-effort - a store/connection failure must not break training.
        logger.warning("mlflow logging failed (%s: %s) - run not tracked", type(exc).__name__, exc)
        return None
