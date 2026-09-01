"""Labeling mechanism + parity with the Phase 1 traffic generator."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from ml.collection.scenarios import (
    ANOMALY_LABELS,
    NORMAL_LABELS,
    SCENARIOS,
    label_to_binary,
)

_GEN_PATH = Path(__file__).resolve().parents[2] / "scripts" / "generate_traffic.py"


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_generate_traffic_probe", _GEN_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclass() needs the module registered
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(spec.name, None)
    return mod


def test_binary_mapping() -> None:
    assert label_to_binary("normal") == 0
    assert label_to_binary("recovery") == 0
    assert label_to_binary("latency_anomaly") == 1
    assert label_to_binary("traffic_surge") == 1
    with pytest.raises(ValueError):
        label_to_binary("nonsense")


def test_label_sets_partition_cleanly() -> None:
    assert NORMAL_LABELS.isdisjoint(ANOMALY_LABELS)
    assert all(label_to_binary(x) == 0 for x in NORMAL_LABELS)
    assert all(label_to_binary(x) == 1 for x in ANOMALY_LABELS)


def test_scenarios_match_phase1_traffic_generator() -> None:
    """The ML scenario definitions must not drift from scripts/generate_traffic.py."""

    gen = _load_generator()
    gen_scenarios = gen.SCENARIOS

    # Names differ slightly (`publish-errors` vs `publish_failure`); match on the
    # injection knobs, which are what actually shape the telemetry.
    def knobs(s: Any) -> tuple[float, int, float, float]:
        return (s.rate_multiplier, s.latency_ms, s.error_rate, s.publish_error_rate)

    ml_knobs = {knobs(s) for s in SCENARIOS.values()}
    gen_knobs = {knobs(s) for s in gen_scenarios.values()}
    assert gen_knobs <= ml_knobs, (
        f"traffic generator has scenario knobs the ML pipeline does not model: "
        f"{gen_knobs - ml_knobs}"
    )
