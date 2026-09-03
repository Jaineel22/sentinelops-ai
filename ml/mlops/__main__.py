"""SentinelOps MLOps CLI (Phase 6).

    python -m ml.mlops register --model-path <path> --run-id <run_id>
    python -m ml.mlops promote  --candidate-version <v> [--reason "..."]
    python -m ml.mlops retrain  --dataset run_a [--seed 42] [--promote-if-passing]
    python -m ml.mlops list-models
    python -m ml.mlops get-champion

Configuration comes from the ``MLFLOW_`` environment (see ``MLflowSettings`` /
``.env.example``). ``promote`` and ``retrain`` run the deterministic evaluation
gate (``ml.mlops.promotion``) and exit non-zero — leaving the champion untouched
— when the candidate does not clear it.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from ml.mlops.config import (
    MLflowSettings,
    ensure_local_tracking_store,
    get_mlflow_settings,
    make_console_emoji_safe,
)
from ml.mlops.promotion import PromotionPolicy, evaluate_candidate, promote_model
from ml.mlops.registry import (
    CHAMPION_ALIAS,
    get_champion_metrics,
    get_model_lineage,
    get_registered_aliases,
    list_model_versions,
    register_model,
    resolve_alias,
)
from ml.mlops.retraining import (
    SUPPORTED_MODEL_TYPES,
    RetrainingConfig,
    RetrainingError,
    retrain_pipeline,
)

_METRIC_SUMMARY_KEYS = (
    "pointwise.f1",
    "pointwise.precision",
    "pointwise.recall",
    "pointwise.pr_auc",
    "pointwise.false_positive_rate",
)


def _ts(millis: int | None) -> str:
    if not millis:
        return "-"
    return datetime.fromtimestamp(millis / 1000, tz=UTC).isoformat()


def _cmd_register(args: argparse.Namespace, settings: MLflowSettings) -> int:
    version = register_model(args.model_path, args.run_id, settings, description=args.description)
    print(f"registered {settings.registered_model_name} v{version} (run {args.run_id})")
    return 0


def _cmd_promote(args: argparse.Namespace, settings: MLflowSettings) -> int:
    candidate_version = str(args.candidate_version)

    champion_version: str | None = None
    try:
        champion_version, _run_id, _uri = resolve_alias(settings, CHAMPION_ALIAS)
    except Exception:
        champion_version = None

    champion_metrics = get_champion_metrics(settings)
    candidate_metrics = get_model_lineage(settings, candidate_version)["metrics"]

    decision = evaluate_candidate(
        candidate_metrics,
        champion_metrics,
        PromotionPolicy(),
        candidate_version=candidate_version,
        champion_version=champion_version,
    )

    against = f"v{champion_version}" if champion_version else "(no champion)"
    print(f"candidate v{candidate_version} vs champion {against}")
    for reason in decision.reasons:
        print(f"  - {reason}")

    if not decision.promote:
        print("DECISION: REJECTED - champion alias unchanged")
        return 1

    new_champion = promote_model(
        settings, candidate_version, reason=args.reason or "; ".join(decision.reasons)
    )
    print(f"DECISION: PROMOTED - champion is now v{new_champion}")
    return 0


def _cmd_list_models(_args: argparse.Namespace, settings: MLflowSettings) -> int:
    versions = list_model_versions(settings)
    if not versions:
        print(f"(no registered versions for {settings.registered_model_name!r})")
        return 0
    # search_model_versions does not populate `.aliases`; read the alias map once.
    aliases_by_version = get_registered_aliases(settings)
    for model_version in sorted(versions, key=lambda mv: int(mv.version)):
        aliases = ", ".join(sorted(aliases_by_version.get(str(model_version.version), []))) or "-"
        print(
            f"v{model_version.version!s:<4} aliases=[{aliases}]  "
            f"run={model_version.run_id}  created={_ts(model_version.creation_timestamp)}"
        )
    return 0


def _cmd_retrain(args: argparse.Namespace, _settings: MLflowSettings) -> int:
    config = RetrainingConfig(
        dataset_id=args.dataset,
        seed=args.seed,
        model_type=args.model_type,
        promote_if_passing=args.promote_if_passing,
    )
    header = (
        f"=== Retraining: dataset={config.dataset_id}, seed={config.seed}, "
        f"model={config.model_type}"
        + (" (auto-promote)" if config.promote_if_passing else "")
        + " ==="
    )
    print(header)
    try:
        result = retrain_pipeline(config, progress=lambda message: print(message))
    except RetrainingError as exc:
        print(f"RETRAINING FAILED: {exc}")
        return 2

    decision = result.promotion_decision
    print(f"DECISION: {'PASS' if decision.promote else 'REJECT'}")
    for reason in decision.reasons:
        print(f"  - {reason}")
    version = result.candidate_version
    if result.promoted:
        print(f"Promotion complete - champion is now v{version}")
    elif decision.promote:
        print(f"Candidate v{version} passed all criteria")
        print(f"To promote, run: python -m ml.mlops promote --candidate-version {version}")
    else:
        print(f"Candidate v{version} rejected - champion unchanged")
    return 0 if decision.promote else 1


def _cmd_get_champion(_args: argparse.Namespace, settings: MLflowSettings) -> int:
    try:
        version, run_id, model_uri = resolve_alias(settings, CHAMPION_ALIAS)
    except Exception as exc:
        print(f"(no champion alias set for {settings.registered_model_name!r}: {exc})")
        return 0

    lineage = get_model_lineage(settings, version)
    print(f"champion: v{version}")
    print(f"  run_id : {run_id}")
    print(f"  uri    : {model_uri}")
    print(f"  created: {_ts(lineage['creation_timestamp'])}")
    for key in _METRIC_SUMMARY_KEYS:
        if key in lineage["metrics"]:
            print(f"  {key:<32} {lineage['metrics'][key]:.4f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ml.mlops", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_register = sub.add_parser("register", help="register a model bundle as a new version")
    p_register.add_argument("--model-path", required=True)
    p_register.add_argument("--run-id", required=True)
    p_register.add_argument("--description", default=None)

    p_promote = sub.add_parser("promote", help="run the gate and promote a candidate if it passes")
    p_promote.add_argument("--candidate-version", required=True)
    p_promote.add_argument("--reason", default=None)

    p_retrain = sub.add_parser(
        "retrain", help="train a fresh model, register it, and run it through the gate"
    )
    p_retrain.add_argument("--dataset", required=True, help="processed run id, e.g. run_a")
    p_retrain.add_argument("--seed", type=int, default=42)
    p_retrain.add_argument(
        "--model-type", default="isolation_forest", choices=SUPPORTED_MODEL_TYPES
    )
    p_retrain.add_argument(
        "--promote-if-passing",
        action="store_true",
        help="promote to champion automatically when the gate passes",
    )

    sub.add_parser("list-models", help="list every version of the registered model")
    sub.add_parser("get-champion", help="show the champion version and its metrics")

    args = parser.parse_args(argv)
    make_console_emoji_safe()
    settings = get_mlflow_settings()
    ensure_local_tracking_store(settings.tracking_uri)

    handlers = {
        "register": _cmd_register,
        "promote": _cmd_promote,
        "retrain": _cmd_retrain,
        "list-models": _cmd_list_models,
        "get-champion": _cmd_get_champion,
    }
    return handlers[args.cmd](args, settings)


if __name__ == "__main__":
    sys.exit(main())
