"""MLflow Model Registry access (Phase 6B).

Runs against a temporary local sqlite MLflow store (tracking + registry); the
whole file is skipped without ``mlflow``.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
from ml.mlops.config import MLflowSettings
from ml.mlops.registry import (
    CHAMPION_ALIAS,
    get_champion_metrics,
    get_model_lineage,
    get_registered_aliases,
    list_model_versions,
    resolve_alias,
    set_alias,
)

from tests.ml.conftest import make_model_version

_HAS_MLFLOW = importlib.util.find_spec("mlflow") is not None
pytestmark = pytest.mark.skipif(not _HAS_MLFLOW, reason="mlflow not installed")


@pytest.fixture
def rsettings(
    mlflow_sqlite: tuple[Path, str],
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> MLflowSettings:
    directory, uri = mlflow_sqlite
    monkeypatch.chdir(directory)
    name = "reg-" + re.sub(r"[^A-Za-z0-9_.-]", "-", request.node.name)
    return MLflowSettings(tracking_uri=uri, registered_model_name=name, experiment_name="p6b-reg")


def test_register_model_creates_version(rsettings: MLflowSettings) -> None:
    version, run_id = make_model_version(rsettings)
    assert version == "1"
    versions = list_model_versions(rsettings)
    # the SQLAlchemy store returns `version` as an int; register_model normalises to str.
    assert [str(v.version) for v in versions] == ["1"]
    assert versions[0].run_id == run_id


def test_list_model_versions_empty_for_unknown_model(rsettings: MLflowSettings) -> None:
    assert list_model_versions(rsettings) == []


def test_resolve_alias_returns_version_run_uri(rsettings: MLflowSettings) -> None:
    version, run_id = make_model_version(rsettings)
    set_alias(rsettings, version, CHAMPION_ALIAS)

    resolved_version, resolved_run, model_uri = resolve_alias(rsettings)
    assert (resolved_version, resolved_run) == (version, run_id)
    assert model_uri == f"models:/{rsettings.registered_model_name}@{CHAMPION_ALIAS}"


def test_resolve_alias_missing_raises(rsettings: MLflowSettings) -> None:
    import mlflow

    make_model_version(rsettings)
    with pytest.raises(mlflow.exceptions.MlflowException):
        resolve_alias(rsettings, CHAMPION_ALIAS)


def test_set_alias_is_repointable(rsettings: MLflowSettings) -> None:
    v1, _ = make_model_version(rsettings)
    v2, _ = make_model_version(rsettings)

    set_alias(rsettings, v1, CHAMPION_ALIAS)
    assert resolve_alias(rsettings)[0] == v1
    set_alias(rsettings, v2, CHAMPION_ALIAS)
    assert resolve_alias(rsettings)[0] == v2


def test_get_champion_metrics_none_when_unset(rsettings: MLflowSettings) -> None:
    make_model_version(rsettings)
    assert get_champion_metrics(rsettings) is None


def test_get_champion_metrics_returns_run_metrics(rsettings: MLflowSettings) -> None:
    version, _ = make_model_version(rsettings, f1=0.83)
    set_alias(rsettings, version, CHAMPION_ALIAS)

    metrics = get_champion_metrics(rsettings)
    assert metrics is not None
    assert metrics["pointwise.f1"] == pytest.approx(0.83)


def test_get_registered_aliases_maps_version_to_aliases(rsettings: MLflowSettings) -> None:
    assert get_registered_aliases(rsettings) == {}

    v1, _ = make_model_version(rsettings)
    v2, _ = make_model_version(rsettings)
    set_alias(rsettings, v1, "previous-champion")
    set_alias(rsettings, v2, CHAMPION_ALIAS)
    set_alias(rsettings, v2, "candidate")

    aliases = get_registered_aliases(rsettings)
    assert aliases[v1] == ["previous-champion"]
    assert sorted(aliases[v2]) == ["candidate", "champion"]


def test_get_model_lineage_is_complete(rsettings: MLflowSettings) -> None:
    version, run_id = make_model_version(rsettings)
    set_alias(rsettings, version, CHAMPION_ALIAS)

    lineage = get_model_lineage(rsettings, version)
    assert lineage["version"] == version
    assert lineage["run_id"] == run_id
    assert lineage["metrics"]["pointwise.f1"] == pytest.approx(0.82)
    assert CHAMPION_ALIAS in lineage["aliases"]
    assert lineage["source"].endswith("/model")
