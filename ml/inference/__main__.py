"""CLI demo of the Phase 3 boundary: load a model, score a processed run's
windows, print JSON-lines of :class:`AnomalyResult`.

    python -m ml.inference <model.joblib> <windows.csv> [--limit N]
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from ml.inference.detector_service import DetectorService


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m ml.inference", description=__doc__)
    p.add_argument("model_path")
    p.add_argument("windows_csv")
    p.add_argument("--limit", type=int, default=20)
    args = p.parse_args(argv)

    svc = DetectorService.load(args.model_path)
    signals = pd.read_csv(args.windows_csv)
    results = svc.score_batch(signals)

    n_flagged = sum(r.is_anomaly for r in results)
    for r in results[: args.limit]:
        print(r.to_json())
    print(
        f"\n# scored {len(results)} windows with {svc.model_type} "
        f"(v{svc.model_version}); flagged {n_flagged}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
