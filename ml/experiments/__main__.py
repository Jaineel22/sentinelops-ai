"""CLI: run one or all experiments and write a summary.

python -m ml.experiments list
python -m ml.experiments run exp2_isolation_forest_sentinelops
python -m ml.experiments run all
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ml.config import RANDOM_SEED, REPORTS_DIR
from ml.experiments.catalog import experiment_specs
from ml.experiments.nab_runner import nab_experiment_specs, run_nab_experiment
from ml.experiments.runner import run_experiment


def _all_specs() -> dict[str, str]:
    out = {name: spec.title for name, spec in experiment_specs().items()}
    out.update({name: spec.title for name, spec in nab_experiment_specs().items()})
    return out


def _run_one(name: str, seed: int) -> dict[str, Any]:
    track_a = experiment_specs()
    if name in track_a:
        return run_experiment(track_a[name], seed=seed)
    return run_nab_experiment(nab_experiment_specs()[name], seed=seed)


def _summary(reports: list[dict[str, Any]]) -> None:
    rows = [
        "# Phase 2 - experiment summary",
        "",
        "| experiment | model | precision | recall | F1 | FPR | PR-AUC | notes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    summary_json: dict[str, Any] = {"seed": RANDOM_SEED, "experiments": {}}
    for rep in reports:
        exp = str(rep["experiment"])
        summary_json["experiments"][exp] = {}
        for model, res in rep["results"].items():
            if "macro_average" in res:  # NAB
                m = res["macro_average"]
                note = "macro-avg over series"
                pw = m
            else:
                pw = res["pointwise"]
                ew = res["eventwise"]
                note = (
                    f"event recall {ew['event_recall']:.2f}, "
                    f"delay {ew['mean_detection_delay_seconds']}s"
                )
            rows.append(
                f"| {exp} | {model} | {pw['precision']:.3f} | {pw['recall']:.3f} | "
                f"{pw['f1']:.3f} | {pw['false_positive_rate']:.3f} | "
                f"{(pw.get('pr_auc') or float('nan')):.3f} | {note} |"
            )
            # Headline numbers only; full detail is in each experiment's metrics.json.
            summary_json["experiments"][exp][model] = {
                k: pw[k]
                for k in ("precision", "recall", "f1", "false_positive_rate", "pr_auc")
                if k in pw
            }
    (REPORTS_DIR / "summary.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (REPORTS_DIR / "summary.json").write_text(json.dumps(summary_json, indent=2), encoding="utf-8")
    print("\n".join(rows))
    print(f"\nsummary -> {REPORTS_DIR / 'summary.md'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ml.experiments", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    run_p = sub.add_parser("run")
    run_p.add_argument("name", help="experiment name, or 'all'")
    run_p.add_argument("--seed", type=int, default=RANDOM_SEED)

    args = parser.parse_args(argv)
    all_specs = _all_specs()

    if args.cmd == "list":
        for name, title in all_specs.items():
            print(f"{name:<38} {title}")
        return 0

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    names = list(all_specs) if args.name == "all" else [args.name]
    reports = [_run_one(name, args.seed) for name in names]
    if len(reports) > 1:
        _summary(reports)
    return 0


if __name__ == "__main__":
    sys.exit(main())
