"""The Track A experiment catalogue (experiments 1-4).

All experiments share one seeded chronological split of ``run_a`` so their
numbers are directly comparable; experiment 4 uses the held-out-fault split
(train on ``run_a`` latency/error faults, test on ``run_b``
publish-failure/surge).

Track B (NAB, experiments 5-6) lives in ``ml.experiments.nab_runner`` because it
is fitted/evaluated per series rather than on one pooled split.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ml.config import CANONICAL_RUN_HOLDOUT, CANONICAL_RUN_MAIN, RANDOM_SEED
from ml.data.prepare import load_processed_run
from ml.data.schema import FEATURE_COLUMNS
from ml.experiments.runner import ExperimentSpec, PreparedData
from ml.features.engineering import build_features
from ml.models import IsolationForestDetector, RandomForestDetector, RobustZScoreDetector
from ml.splits import Split, chronological_split, held_out_fault_split

# --- shared model factories (each receives the experiment's feature-name list) --
_BASELINE = ("robust_zscore", lambda fn: RobustZScoreDetector(feature_names=fn))
_IFOREST = (
    "isolation_forest",
    lambda fn: IsolationForestDetector(
        feature_names=fn, random_state=RANDOM_SEED, n_estimators=200
    ),
)
_RF = (
    "random_forest_supervised",
    lambda fn: RandomForestDetector(feature_names=fn, random_state=RANDOM_SEED),
)


def _prepared(
    result: Split, feature_names: list[str], window_seconds: float, **notes: object
) -> PreparedData:
    def xy(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
        return df, df["is_anomaly"].astype(int)

    xtr, ytr = xy(result.train)
    xva, yva = xy(result.val)
    xte, yte = xy(result.test)
    return PreparedData(
        x_train=xtr,
        y_train=ytr,
        x_val=xva,
        y_val=yva,
        x_test=xte,
        y_test=yte,
        window_seconds=window_seconds,
        feature_names=feature_names,
        notes=dict(notes),
    )


# --- Track A -------------------------------------------------------------
# The `main` collection plan is 3 identical cycles of
# [normal, latency, normal, errors, normal, surge, normal, recovery].
# A 50/17/33 chronological split therefore puts the whole final cycle - every
# fault type - in the test set, while train/val still see each fault earlier.
_VAL_FRACTION = 0.17
_TEST_FRACTION = 0.33


def _track_a_chrono() -> PreparedData:
    raw = load_processed_run(CANONICAL_RUN_MAIN)
    feats = build_features(raw)
    window_seconds = float(np.median(raw["window_seconds"]))
    split = chronological_split(feats, val_fraction=_VAL_FRACTION, test_fraction=_TEST_FRACTION)
    return _prepared(
        split,
        list(FEATURE_COLUMNS),
        window_seconds,
        dataset=f"sentinelops/{CANONICAL_RUN_MAIN}",
        split=f"chronological {round((1 - _VAL_FRACTION - _TEST_FRACTION) * 100)}/"
        f"{round(_VAL_FRACTION * 100)}/{round(_TEST_FRACTION * 100)}",
    )


def _track_a_holdout() -> PreparedData:
    train_src = build_features(load_processed_run(CANONICAL_RUN_MAIN))
    test_src = build_features(load_processed_run(CANONICAL_RUN_HOLDOUT))
    window_seconds = float(np.median(load_processed_run(CANONICAL_RUN_MAIN)["window_seconds"]))
    split = held_out_fault_split(
        train_src,
        test_src,
        train_faults={"latency_anomaly", "error_anomaly"},
        holdout_faults={"publish_failure", "traffic_surge"},
        val_fraction=0.25,
    )
    return _prepared(
        split,
        list(FEATURE_COLUMNS),
        window_seconds,
        dataset=f"train=sentinelops/{CANONICAL_RUN_MAIN} (latency,error) | "
        f"test=sentinelops/{CANONICAL_RUN_HOLDOUT} (publish_failure,surge)",
        split="held-out fault type",
    )


# --- catalogue -------------------------------------------------------
def experiment_specs() -> dict[str, ExperimentSpec]:
    specs = [
        ExperimentSpec(
            name="exp1_baseline_sentinelops",
            title="Exp 1 - Statistical baseline on SentinelOps telemetry",
            rationale="Establish a robust, unsupervised reference (median/MAD z-score) "
            "before any ML model.",
            build_data=_track_a_chrono,
            models=dict([_BASELINE]),
        ),
        ExperimentSpec(
            name="exp2_isolation_forest_sentinelops",
            title="Exp 2 - Isolation Forest on SentinelOps telemetry",
            rationale="Primary detector: multivariate, semi-supervised (trained on normal "
            "windows), deterministic.",
            build_data=_track_a_chrono,
            models=dict([_IFOREST]),
        ),
        ExperimentSpec(
            name="exp3_comparison_sentinelops",
            title="Exp 3 - Baseline vs Isolation Forest vs supervised RF",
            rationale="Same chronological split, three detectors. Shows the ML lift over the "
            "baseline and the ceiling a supervised model reaches with labels.",
            build_data=_track_a_chrono,
            models=dict([_BASELINE, _IFOREST, _RF]),
            supervised_models=frozenset({"random_forest_supervised"}),
        ),
        ExperimentSpec(
            name="exp4_heldout_fault_sentinelops",
            title="Exp 4 - Held-out fault type (generalization)",
            rationale="Train on latency + error faults only; test on publish-failure + "
            "traffic-surge, never seen in training. Can the detector flag an unfamiliar "
            "failure mode?",
            build_data=_track_a_holdout,
            models=dict([_BASELINE, _IFOREST, _RF]),
            supervised_models=frozenset({"random_forest_supervised"}),
        ),
    ]
    return {s.name: s for s in specs}
