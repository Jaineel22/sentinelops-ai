"""Central configuration for the ML subsystem: seeds and repo-relative paths.

Everything reproducible flows from :data:`RANDOM_SEED`. Paths are resolved from
this file's location so the pipeline runs the same regardless of the working
directory.
"""

from __future__ import annotations

import random
from pathlib import Path

RANDOM_SEED = 42

# ml/  ->  repo root
ML_DIR = Path(__file__).resolve().parent
REPO_ROOT = ML_DIR.parent

DATA_DIR = ML_DIR / "data"
RAW_DIR = DATA_DIR / "raw"  # git-ignored (large / regenerable)
PROCESSED_DIR = DATA_DIR / "processed"  # committed (small canonical datasets)
NAB_RAW_DIR = RAW_DIR / "nab"  # git-ignored (AGPL-3.0 — not redistributed)

ARTIFACTS_DIR = REPO_ROOT / "artifacts"
MODELS_DIR = ARTIFACTS_DIR / "models"  # git-ignored
REPORTS_DIR = ARTIFACTS_DIR / "reports"  # committed (recruiter-readable results)

# The two canonical Track A runs used for the reported results.
SENTINELOPS_PROCESSED_DIR = PROCESSED_DIR / "sentinelops"
CANONICAL_RUN_MAIN = "run_a"  # normal / latency / errors / recovery / surge
CANONICAL_RUN_HOLDOUT = "run_b"  # normal / publish_failure / surge (held-out test)


def set_global_seed(seed: int = RANDOM_SEED) -> None:
    """Seed Python and NumPy RNGs. scikit-learn estimators take ``random_state``
    explicitly; this covers everything else."""

    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except ModuleNotFoundError:  # numpy is an optional (`ml`) dependency
        pass
