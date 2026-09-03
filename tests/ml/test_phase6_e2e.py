"""Phase 6F - the end-to-end demo actually runs and reaches the expected state.

Runs `scripts/phase6_e2e_demo.py` as a subprocess against a throwaway sqlite
MLflow store. Tagged ``mlflow``; skipped without ``mlflow``.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HAS_MLFLOW = importlib.util.find_spec("mlflow") is not None
pytestmark = [
    pytest.mark.skipif(not _HAS_MLFLOW, reason="mlflow not installed"),
    pytest.mark.mlflow,
]

_DEMO = Path(__file__).resolve().parents[2] / "scripts" / "phase6_e2e_demo.py"


def test_phase6_e2e_demo_completes(tmp_path: Path) -> None:
    env = {
        "MLFLOW_TRACKING_URI": f"sqlite:///{(tmp_path / 'p6.db').as_posix()}",
        "MLFLOW_REGISTERED_MODEL_NAME": "sentinelops-anomaly-detector",
        "MLFLOW_EXPERIMENT_NAME": "phase6-e2e-test",
    }
    completed = subprocess.run(
        [sys.executable, str(_DEMO)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**os.environ, **env},
        timeout=600,
    )
    out = completed.stdout + completed.stderr

    assert completed.returncode == 0, out
    assert "=== Phase 6 End-to-End Demo ===" in out
    assert "model_source: registry" in out
    assert "significant_drift" in out
    assert "gate: PASS" in out
    assert "PASS - Phase 6 end-to-end flow verified." in out
