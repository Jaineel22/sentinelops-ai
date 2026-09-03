"""Deterministic model-promotion gate (Phase 6B).

A newly trained model does **not** become the champion just because training
succeeded. :func:`evaluate_candidate` is a pure, deterministic function over the
candidate's and the current champion's evaluation metrics (as produced by
``ml.evaluation`` and logged by ``ml.mlops.tracking``). No LLM, no randomness,
no network. :func:`promote_model` only moves the ``champion`` alias when the gate
returns ``promote=True``.

Criteria and their justification: [ADR-033](../../docs/decisions/adr-033-promotion-criteria.md).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ml.mlops.config import MLflowSettings
from ml.mlops.registry import (
    CANDIDATE_ALIAS,
    CHAMPION_ALIAS,
    PREVIOUS_CHAMPION_ALIAS,
    add_version_tag,
    get_model_baseline,
    resolve_alias,
    set_alias,
    set_model_baseline,
)

if TYPE_CHECKING:
    import numpy as np
    import pandas as pd

    from ml.monitoring.baseline import BaselineDistribution

logger = logging.getLogger("ml.mlops.promotion")

# Metric keys as flattened by ml.mlops.tracking.log_run (ml.evaluation output).
F1_KEY = "pointwise.f1"
RECALL_KEY = "pointwise.recall"
PR_AUC_KEY = "pointwise.pr_auc"
_REQUIRED_METRICS = (F1_KEY, RECALL_KEY, PR_AUC_KEY)


@dataclass(frozen=True)
class PromotionPolicy:
    """Absolute quality floors + a no-regression guard. Defaults are grounded in
    the committed Phase 2 numbers (ADR-033): IF exp2 F1 0.82 / recall 1.00,
    exp4 F1 0.86. Every field is overridable."""

    min_f1: float = 0.75
    min_recall: float = 0.90
    min_pr_auc: float = 0.60
    f1_regression_tolerance: float = 0.05
    require_all_metrics: bool = True


@dataclass(frozen=True)
class PromotionDecision:
    promote: bool
    reasons: list[str]
    candidate_version: str
    champion_version: str | None


def _metric(metrics: Mapping[str, object], key: str) -> float | None:
    value = metrics.get(key)
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def evaluate_candidate(
    candidate_metrics: Mapping[str, object],
    champion_metrics: Mapping[str, object] | None,
    policy: PromotionPolicy | None = None,
    *,
    candidate_version: str = "?",
    champion_version: str | None = None,
) -> PromotionDecision:
    """Decide whether ``candidate_metrics`` clears the gate.

    * evaluation completeness — F1, recall and PR-AUC must all be present
      (when ``policy.require_all_metrics``);
    * absolute floors — F1 ≥ ``min_f1``, recall ≥ ``min_recall``,
      PR-AUC ≥ ``min_pr_auc``;
    * no regression — vs an existing champion, F1 may not drop by more than
      ``f1_regression_tolerance``.

    With no champion the regression check is skipped (first model), but the
    floors and completeness checks still apply.
    """

    policy = policy or PromotionPolicy()
    reasons: list[str] = []

    present = {k: _metric(candidate_metrics, k) for k in _REQUIRED_METRICS}
    missing = [k for k, v in present.items() if v is None]
    if missing and policy.require_all_metrics:
        reasons.append(f"incomplete evaluation - missing metric(s): {', '.join(sorted(missing))}")
        return PromotionDecision(False, reasons, candidate_version, champion_version)

    f1 = present[F1_KEY] or 0.0
    recall = present[RECALL_KEY] or 0.0
    pr_auc = present[PR_AUC_KEY] or 0.0

    if f1 < policy.min_f1:
        reasons.append(f"F1 {f1:.4f} below floor {policy.min_f1:.2f}")
    if recall < policy.min_recall:
        reasons.append(f"recall {recall:.4f} below floor {policy.min_recall:.2f}")
    if pr_auc < policy.min_pr_auc:
        reasons.append(f"PR-AUC {pr_auc:.4f} below floor {policy.min_pr_auc:.2f}")

    champion_f1 = _metric(champion_metrics, F1_KEY) if champion_metrics else None
    if champion_f1 is not None:
        delta = f1 - champion_f1
        if delta < -policy.f1_regression_tolerance:
            reasons.append(
                f"F1 regression {delta:+.4f} vs champion "
                f"(tolerance -{policy.f1_regression_tolerance:.2f})"
            )

    if reasons:
        return PromotionDecision(False, reasons, candidate_version, champion_version)

    ok: list[str] = [f"F1 {f1:.4f} / recall {recall:.4f} / PR-AUC {pr_auc:.4f} meet all floors"]
    if champion_f1 is None:
        ok.append("no prior champion - first model")
    else:
        ok.append(f"F1 {f1 - champion_f1:+.4f} vs champion (within tolerance)")
    return PromotionDecision(True, ok, candidate_version, champion_version)


def promote_model(
    settings: MLflowSettings,
    candidate_version: str | int,
    *,
    reason: str | None = None,
    baseline: BaselineDistribution | None = None,
) -> str:
    """Move the aliases so ``candidate_version`` becomes the champion.

    ``champion`` -> candidate; ``candidate`` -> candidate; and, if there was a
    different champion, ``previous-champion`` -> the old one. The evaluation gate
    (:func:`evaluate_candidate`) is the caller's responsibility — the CLI runs it
    before calling this.

    Phase 6D: if ``baseline`` is given it is stored with the new champion for
    drift detection; otherwise, if the version's source run already carries a
    baseline (logged at training time) that is used, and if neither exists a
    warning is logged (promotion still succeeds).
    """

    candidate_version = str(candidate_version)

    old_champion: str | None = None
    try:
        old_champion, _run_id, _uri = resolve_alias(settings, CHAMPION_ALIAS)
    except Exception:
        old_champion = None

    if old_champion and old_champion != candidate_version:
        set_alias(settings, old_champion, PREVIOUS_CHAMPION_ALIAS)

    set_alias(settings, candidate_version, CANDIDATE_ALIAS)
    set_alias(settings, candidate_version, CHAMPION_ALIAS)

    add_version_tag(settings, candidate_version, "promotion.reason", reason or "(unspecified)")
    if old_champion:
        add_version_tag(settings, candidate_version, "promotion.previous_champion", old_champion)

    _ensure_champion_baseline(settings, candidate_version, baseline)

    logger.info(
        "promoted %s v%s to champion (previous: %s); reason: %s",
        settings.registered_model_name,
        candidate_version,
        old_champion or "none",
        reason or "(unspecified)",
    )
    return candidate_version


def _ensure_champion_baseline(
    settings: MLflowSettings, version: str, baseline: BaselineDistribution | None
) -> None:
    if baseline is not None:
        try:
            set_model_baseline(settings, version, baseline)
        except Exception as exc:
            logger.warning("could not store drift baseline for champion v%s (%s)", version, exc)
        return
    try:
        if get_model_baseline(settings, version) is not None:
            return
    except Exception:
        pass
    logger.warning(
        "champion v%s has no drift baseline - run `python -m ml.monitoring baseline ...` or "
        "pass baseline= to promote_model; drift detection is unavailable until one exists",
        version,
    )


def _store_baseline(
    settings: MLflowSettings,
    version: str | int,
    x_train: pd.DataFrame | np.ndarray,
    feature_names: list[str],
    *,
    feature_schema_version: str | None = None,
) -> BaselineDistribution:
    """Freeze a baseline from ``x_train`` (training features only) and store it
    with model ``version``. Returns it."""

    from ml.data.schema import FEATURE_SCHEMA_VERSION
    from ml.monitoring.baseline import freeze_baseline

    baseline = freeze_baseline(
        x_train,
        feature_names=list(feature_names),
        model_version=str(version),
        feature_schema_version=feature_schema_version or FEATURE_SCHEMA_VERSION,
    )
    set_model_baseline(settings, version, baseline)
    return baseline
