"""Manual drift-monitoring CLI (Phase 6D).

    python -m ml.monitoring baseline --model-version 1 \
        --data ml/data/processed/sentinelops/run_a/windows.csv \
        --output artifacts/models/run_a__baseline.joblib

    python -m ml.monitoring check \
        --baseline artifacts/models/run_a__baseline.joblib \
        --data ml/data/processed/sentinelops/run_b/windows.csv \
        --output artifacts/reports/drift_run_b.json

``--data`` is a processed windows CSV (signal columns); features are rebuilt with
the Phase 2 pipeline so the baseline and the check see identical feature
engineering. Exit code is non-zero from ``check`` when drift is ``significant``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from ml.data.schema import FEATURE_COLUMNS, FEATURE_SCHEMA_VERSION
from ml.features.engineering import build_features
from ml.monitoring.baseline import freeze_baseline, load_baseline, save_baseline
from ml.monitoring.drift import detect_drift


def _features(data_path: str) -> pd.DataFrame:
    raw = pd.read_csv(data_path)
    return build_features(raw)[list(FEATURE_COLUMNS)]


def _cmd_baseline(args: argparse.Namespace) -> int:
    baseline = freeze_baseline(
        _features(args.data),
        feature_names=list(FEATURE_COLUMNS),
        model_version=args.model_version,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
    )
    path = save_baseline(baseline, args.output)
    print(
        f"baseline: {baseline.n_samples} samples, {len(baseline.feature_names)} features -> {path}"
    )
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    baseline = load_baseline(args.baseline)
    report = detect_drift(_features(args.data), baseline, model_version=baseline.model_version)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")

    print(f"overall: {report.overall_decision}  (n={report.n_samples_current})")
    for feature in sorted(report.feature_reports, key=lambda r: r.psi, reverse=True):
        print(f"  {feature.feature_name:<32} PSI={feature.psi:.4f}  {feature.classification}")
    if report.missing_features:
        print(f"  (missing from current data: {', '.join(report.missing_features)})")
    return 1 if report.overall_decision == "significant_drift" else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ml.monitoring", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_baseline = sub.add_parser("baseline", help="freeze a reference distribution from a dataset")
    p_baseline.add_argument("--model-version", required=True)
    p_baseline.add_argument("--data", required=True)
    p_baseline.add_argument("--output", required=True)

    p_check = sub.add_parser("check", help="compare a dataset against a saved baseline")
    p_check.add_argument("--baseline", required=True)
    p_check.add_argument("--data", required=True)
    p_check.add_argument("--output", default=None)

    args = parser.parse_args(argv)
    return {"baseline": _cmd_baseline, "check": _cmd_check}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
