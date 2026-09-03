"""Deterministic promotion gate + alias promotion (Phase 6B).

`evaluate_candidate` is pure and always tested. `promote_model` runs against a
temporary local sqlite MLflow registry and is skipped without `mlflow`.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
from ml.mlops.config import MLflowSettings
from ml.mlops.promotion import (
    PromotionDecision,
    PromotionPolicy,
    evaluate_candidate,
    promote_model,
)
from ml.mlops.registry import (
    CANDIDATE_ALIAS,
    CHAMPION_ALIAS,
    PREVIOUS_CHAMPION_ALIAS,
    resolve_alias,
)

from tests.ml.conftest import make_model_version

_HAS_MLFLOW = importlib.util.find_spec("mlflow") is not None
_needs_mlflow = pytest.mark.skipif(not _HAS_MLFLOW, reason="mlflow not installed")

_POLICY = PromotionPolicy()
_GOOD: dict[str, object] = {"pointwise.f1": 0.82, "pointwise.recall": 1.0, "pointwise.pr_auc": 0.70}


# --- pure gate ------------------------------------------------------------------
def test_pass_when_all_criteria_met() -> None:
    champion = {"pointwise.f1": 0.80, "pointwise.recall": 1.0, "pointwise.pr_auc": 0.68}
    decision = evaluate_candidate(_GOOD, champion, _POLICY)
    assert isinstance(decision, PromotionDecision)
    assert decision.promote and decision.reasons


def test_pass_first_model_when_no_champion() -> None:
    decision = evaluate_candidate(_GOOD, None, _POLICY)
    assert decision.promote
    assert any("first model" in r for r in decision.reasons)


def test_fail_f1_below_floor() -> None:
    decision = evaluate_candidate({**_GOOD, "pointwise.f1": 0.60}, None, _POLICY)
    assert not decision.promote
    assert any("F1" in r and "floor" in r for r in decision.reasons)


def test_fail_recall_below_floor() -> None:
    decision = evaluate_candidate({**_GOOD, "pointwise.recall": 0.70}, None, _POLICY)
    assert not decision.promote
    assert any("recall" in r for r in decision.reasons)


def test_fail_pr_auc_below_floor() -> None:
    decision = evaluate_candidate({**_GOOD, "pointwise.pr_auc": 0.40}, None, _POLICY)
    assert not decision.promote
    assert any("PR-AUC" in r for r in decision.reasons)


def test_fail_f1_regression_beyond_tolerance() -> None:
    champion = {"pointwise.f1": 0.92, "pointwise.recall": 1.0, "pointwise.pr_auc": 0.80}
    candidate = {"pointwise.f1": 0.80, "pointwise.recall": 1.0, "pointwise.pr_auc": 0.70}
    decision = evaluate_candidate(candidate, champion, _POLICY)
    assert not decision.promote
    assert any("regression" in r for r in decision.reasons)


def test_pass_small_f1_drop_within_tolerance() -> None:
    champion = {"pointwise.f1": 0.84, "pointwise.recall": 1.0, "pointwise.pr_auc": 0.80}
    candidate = {"pointwise.f1": 0.81, "pointwise.recall": 1.0, "pointwise.pr_auc": 0.70}
    assert evaluate_candidate(candidate, champion, _POLICY).promote


def test_fail_missing_metrics() -> None:
    decision = evaluate_candidate({"pointwise.f1": 0.82}, None, _POLICY)
    assert not decision.promote
    assert any("missing" in r for r in decision.reasons)


def test_missing_metrics_allowed_when_policy_relaxed() -> None:
    lenient = PromotionPolicy(require_all_metrics=False, min_recall=0.0, min_pr_auc=0.0)
    decision = evaluate_candidate({"pointwise.f1": 0.90}, None, lenient)
    assert decision.promote


def test_decision_carries_versions() -> None:
    decision = evaluate_candidate(
        _GOOD, None, _POLICY, candidate_version="7", champion_version=None
    )
    assert decision.candidate_version == "7"
    assert decision.champion_version is None


# --- alias promotion (needs mlflow) ------------------------------------------
@pytest.fixture
def psettings(
    mlflow_sqlite: tuple[Path, str],
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> MLflowSettings:
    if not _HAS_MLFLOW:
        pytest.skip("mlflow not installed")
    directory, uri = mlflow_sqlite
    monkeypatch.chdir(directory)
    name = "promo-" + re.sub(r"[^A-Za-z0-9_.-]", "-", request.node.name)
    return MLflowSettings(tracking_uri=uri, registered_model_name=name, experiment_name="p6b-promo")


def _aliases(settings: MLflowSettings, version: str) -> set[str]:
    from mlflow.tracking import MlflowClient

    model_version = MlflowClient(settings.tracking_uri).get_model_version(
        settings.registered_model_name, str(version)
    )
    return set(model_version.aliases)


@_needs_mlflow
def test_promote_first_model_sets_candidate_and_champion(psettings: MLflowSettings) -> None:
    import mlflow

    version, _run_id = make_model_version(psettings)
    promote_model(psettings, version, reason="first model")

    assert _aliases(psettings, version) == {CANDIDATE_ALIAS, CHAMPION_ALIAS}
    with pytest.raises(mlflow.exceptions.MlflowException):
        resolve_alias(psettings, PREVIOUS_CHAMPION_ALIAS)


@_needs_mlflow
def test_promote_second_model_moves_previous_champion(psettings: MLflowSettings) -> None:
    v1, _ = make_model_version(psettings)
    promote_model(psettings, v1)
    v2, _ = make_model_version(psettings)
    promote_model(psettings, v2, reason="better F1")

    assert _aliases(psettings, v2) == {CANDIDATE_ALIAS, CHAMPION_ALIAS}
    assert PREVIOUS_CHAMPION_ALIAS in _aliases(psettings, v1)
    assert resolve_alias(psettings, CHAMPION_ALIAS)[0] == v2
    assert resolve_alias(psettings, PREVIOUS_CHAMPION_ALIAS)[0] == v1
