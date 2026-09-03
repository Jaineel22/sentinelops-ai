"""Reference feature distribution ("baseline") for drift detection (Phase 6D).

A :class:`BaselineDistribution` is frozen from a model's **training features**
(never labels, never production data) at training / promotion time and stored as
an artifact alongside the model version. :mod:`ml.monitoring.drift` compares a
later window of production features against it.

Binning is **per-feature quantile binning** (default 10 bins ≈ deciles of the
reference), not equal-width: PSI's standard interpretation bands assume roughly
equal-mass reference bins (see ADR-034). Bin edges are stored so the comparison
window is bucketed identically; production values outside the reference range are
folded into the end bins by the drift detector.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

DEFAULT_N_BINS = 10
_QUANTILES = ("p05", "p25", "p50", "p75", "p95")


class BaselineError(ValueError):
    """Invalid input to baseline construction."""


@dataclass
class BaselineDistribution:
    feature_names: list[str]
    bin_edges: dict[str, list[float]]
    reference_proportions: dict[str, list[float]]
    statistics: dict[str, dict[str, float]]
    n_samples: int
    model_version: str
    feature_schema_version: str
    n_bins: int = DEFAULT_N_BINS
    created_at: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BaselineDistribution:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def _as_frame(x: pd.DataFrame | np.ndarray, feature_names: list[str]) -> pd.DataFrame:
    if isinstance(x, pd.DataFrame):
        missing = [c for c in feature_names if c not in x.columns]
        if missing:
            raise BaselineError(f"training features missing columns: {missing}")
        return x.loc[:, feature_names].astype(float)
    arr = np.asarray(x, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != len(feature_names):
        raise BaselineError(
            f"array shape {arr.shape} does not match {len(feature_names)} feature names"
        )
    return pd.DataFrame(arr, columns=feature_names)


def _bin_edges(values: np.ndarray, n_bins: int) -> list[float]:
    """Quantile edges, de-duplicated. A (near-)constant feature collapses to one
    bin spanning its single value."""

    qs = np.linspace(0.0, 1.0, n_bins + 1)
    edges = np.unique(np.quantile(values, qs))
    if edges.size < 2:
        centre = float(edges[0]) if edges.size else 0.0
        span = abs(centre) * 1e-6 + 1e-9
        edges = np.array([centre - span, centre + span])
    return [float(e) for e in edges]


def _proportions(values: np.ndarray, edges: list[float]) -> list[float]:
    edge_arr = np.asarray(edges, dtype=float)
    clipped = np.clip(values, edge_arr[0], edge_arr[-1])
    counts, _ = np.histogram(clipped, bins=edge_arr)
    total = float(counts.sum())
    if total <= 0:
        uniform = 1.0 / len(counts)
        return [uniform for _ in counts]
    return [float(c) / total for c in counts]


def feature_statistics(values: np.ndarray) -> dict[str, float]:
    values = np.asarray(values, dtype=float)
    stats: dict[str, float] = {
        "mean": float(np.mean(values)),
        "std": float(np.std(values)),
        "min": float(np.min(values)),
        "max": float(np.max(values)),
    }
    for name, q in zip(_QUANTILES, (0.05, 0.25, 0.50, 0.75, 0.95), strict=True):
        stats[name] = float(np.quantile(values, q))
    return stats


def freeze_baseline(
    x: pd.DataFrame | np.ndarray,
    feature_names: list[str],
    model_version: str,
    feature_schema_version: str,
    *,
    n_bins: int = DEFAULT_N_BINS,
) -> BaselineDistribution:
    """Build a :class:`BaselineDistribution` from training features.

    ``x`` is a feature frame (or 2-D array + ``feature_names``). Labels must not
    be included — this is the reference the drift detector compares against.
    """

    if not feature_names:
        raise BaselineError("feature_names is empty")
    if n_bins < 2:
        raise BaselineError("n_bins must be >= 2")

    frame = _as_frame(x, feature_names)
    if len(frame) == 0:
        raise BaselineError("training feature frame has no rows")

    bin_edges: dict[str, list[float]] = {}
    reference_proportions: dict[str, list[float]] = {}
    statistics: dict[str, dict[str, float]] = {}
    for name in feature_names:
        col = frame[name].to_numpy(dtype=float)
        edges = _bin_edges(col, n_bins)
        bin_edges[name] = edges
        reference_proportions[name] = _proportions(col, edges)
        statistics[name] = feature_statistics(col)

    return BaselineDistribution(
        feature_names=list(feature_names),
        bin_edges=bin_edges,
        reference_proportions=reference_proportions,
        statistics=statistics,
        n_samples=len(frame),
        model_version=str(model_version),
        feature_schema_version=str(feature_schema_version),
        n_bins=n_bins,
    )


def save_baseline(baseline: BaselineDistribution, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"class": "BaselineDistribution", "data": baseline.to_dict()}, path)
    return path


def load_baseline(path: str | Path) -> BaselineDistribution:
    bundle = joblib.load(Path(path))
    if isinstance(bundle, dict) and bundle.get("class") == "BaselineDistribution":
        return BaselineDistribution.from_dict(bundle["data"])
    if isinstance(bundle, dict):  # a bare to_dict() payload
        return BaselineDistribution.from_dict(bundle)
    raise BaselineError(f"{path} is not a saved BaselineDistribution")
