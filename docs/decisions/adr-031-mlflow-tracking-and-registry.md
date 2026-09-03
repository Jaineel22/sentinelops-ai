# ADR-031: MLflow for experiment tracking and the model registry

- Status: Accepted
- Date: 2026-09-03

## Context

Through Phase 2 the ML workflow is *train → save a `.joblib` bundle → load it in
the anomaly-detector*. `ml/experiments/runner.py` already records a good deal per
run (git SHA, Python version, seed, split sizes, the full metric set) into
`artifacts/reports/<experiment>/metrics.json`, and `artifacts/reports/summary.md`
is committed. But there is:

- no queryable history of runs or a way to compare them;
- no model **registry** — nothing that answers "which model version is live, and
  which run produced it?";
- no alias-based promotion, so the live model is a hard-coded file path
  (`DETECTOR_MODEL_PATH`) or an on-startup retrain;
- no first-class lineage linking a deployed model back to its dataset, feature
  schema, code version, and metrics.

Phase 6 needs all of that without disturbing the Phase 2 detector interface
(`fit / score_samples / predict / save / load`), its evaluation methodology
(`ml/evaluation/`, authoritative), or the pandas/numpy versions Phase 2 was
trained and evaluated on.

## Decision

Adopt **MLflow** as the experiment-tracking and model-registry system, deployed
locally via Docker Compose.

- **Client:** the `mlops` optional-dependency group installs **`mlflow-skinny`**
  only — the tracking/registry client. It does **not** pull `mlflow`'s server
  stack and, critically, does **not** downgrade `pandas` (the full `mlflow`
  package pins `pandas<3`, which would change the Phase 2 foundation).
- **Server:** a `mlflow` Compose service built from `docker/mlflow/Dockerfile`
  (`ghcr.io/mlflow/mlflow` + a pinned `psycopg2-binary`). UI + REST API on
  `http://localhost:5000`.
- **Backend store:** a dedicated **`mlflow` database on the existing PostgreSQL
  instance** — a database-backed store is required for the model registry, and
  reusing the running Postgres avoids introducing another engine. A one-shot
  `mlflow-init` service (`postgres:17-alpine` + `psql`) creates that database
  idempotently, mirroring the `*-migrate` pattern.
- **Artifact store:** the `mlflow-artifacts` named volume, served over HTTP by
  the tracking server (`--serve-artifacts --artifacts-destination`). Clients
  speak only HTTP — no shared filesystem mount, no cloud object storage.
- **Tracking is additive and fail-safe.** `ml.mlops.tracking.log_run` mirrors
  each model's run into MLflow; `metrics.json` / `summary.md` remain the
  authoritative record and are written exactly as before. `run_experiment` only
  attempts MLflow when `MLFLOW_TRACKING_URI` is set in the environment, so the
  offline pipeline, CI, and the reproducibility tests stay side-effect-free by
  default. A missing install or an unreachable store logs a warning and is
  skipped — never raised, never fabricated.
- **Configuration** is a typed `MLflowSettings` (`MLFLOW_` prefix): tracking URI,
  registered model name, model alias, `required` flag, experiment name.
- **Registry model flavor:** a model version points at the **existing joblib
  bundle** logged as a plain artifact; it is loaded back with the Phase 2
  `AnomalyDetector.load`. No `mlflow.pyfunc` / `mlflow.sklearn` wrapper — the
  registry manages *versions and aliases*, it is not an execution mechanism, and
  no artifact is ever loaded as arbitrary code from an untrusted source
  (Sub-phase 6B).
- **Aliases, not stages.** Promotion uses MLflow **model aliases**
  (`candidate`, `champion`), never the deprecated `Staging` / `Production`
  stages. The alias strategy and the deterministic promotion gate are their own
  decisions (Sub-phases 6B/6E).

For local/offline use and tests the tracking URI is a `sqlite:///` file — enough
for the registry, no server needed.

## Alternatives considered

- **Weights & Biases / Neptune / Comet.** Hosted-first; a free tier with
  meaningful limits and an external dependency for what is a local portfolio
  project. MLflow is OSS, self-hostable, and the de-facto standard.
- **Full `mlflow` package in the app venv.** Cleanest API surface, but it pins
  `pandas<3` and drags in Flask/gunicorn/graphene the client does not need.
  `mlflow-skinny` gives the same tracking + registry client without either cost.
- **Keep the bespoke `metrics.json` tracker and add a small SQL store.**
  Reinvents runs/params/metrics/artifacts/registry/aliases/UI that MLflow already
  provides and interviewers already recognise.
- **File-based MLflow store (`./mlruns`).** Simplest, but cannot hold registered
  models or aliases — the core of Phase 6B. Kept only as the test/offline URI.
- **A separate SQLite file for the server backend store.** Works, but single-
  writer and a second database technology in Compose; the existing Postgres is
  right there.

## Consequences

- New optional-dependency group `mlops` (`mlflow-skinny`); `packaging` and
  `protobuf` are pinned slightly lower by the resolver (no functional impact;
  pandas/numpy untouched).
- Two new Compose services (`mlflow-init`, `mlflow`), one new volume
  (`mlflow-artifacts`), one new image (`docker/mlflow/Dockerfile`). Nothing in
  Phases 1–5 depends on them; `docker compose up` of the existing chain is
  unchanged.
- `ml/experiments/runner.py` gains an opt-in MLflow logging step; its existing
  outputs are byte-for-byte unchanged when `MLFLOW_TRACKING_URI` is unset.
- `.env.example` documents the `MLFLOW_` variables; `.gitignore` excludes
  `mlruns/`, `mlartifacts/`, and local `*.db` files. No credentials are
  committed — the server uses the throwaway dev Postgres pair already in
  `docker-compose.yml`.
- Later sub-phases build on this: 6B (registry + aliases + promotion gate), 6C
  (inference resolves the alias), 6D (drift), 6E (retraining), 6F (docs + e2e).

## References

- MLflow Tracking — https://mlflow.org/docs/latest/tracking.html
- MLflow Model Registry & aliases — https://mlflow.org/docs/latest/model-registry.html
- `mlflow-skinny` — https://mlflow.org/docs/latest/tracking.html#tracking-of-the-model
