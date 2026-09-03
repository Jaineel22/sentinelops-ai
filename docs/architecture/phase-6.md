# Phase 6 — MLOps Lifecycle

> Status: **complete.** Sub-phases 6A (MLflow infrastructure + experiment
> tracking), 6B (model registry + alias-based promotion gate), 6C (inference
> integration), 6D (monitoring + drift detection), 6E (reproducible retraining
> workflow) and 6F (documentation + end-to-end validation) are all implemented
> and tested. A one-line summary for reviewers:
> [docs/phase6-summary.md](../phase6-summary.md).

## 1. Overview

Phase 2 built a real, evaluated anomaly detector but stopped at *train → save a
`.joblib` → load it*. Phase 6 evolves that into a reproducible, versioned,
observable **MLOps lifecycle** around the same Isolation Forest detector and the
same Phase 2 evaluation methodology — which stays authoritative. Nothing here
replaces the model with an LLM, adds deep-learning models, or changes the
detector interface (`fit / score_samples / predict / save / load`).

```
dataset → feature generation → training → experiment tracking (MLflow) →
evaluation (Phase 2 framework) → model registration → model version →
alias-based promotion (candidate / champion) → inference using the selected model →
monitoring → drift detection → retraining recommendation/workflow → new version
```

## 2. Goals

1. **Experiment tracking** — every training run's parameters, metrics, artifacts,
   and lineage recorded in MLflow (MLflow *records* the Phase 2 evaluation
   output, it does not replace the framework).
2. **Model registry with alias-based promotion** — versioned models promoted to
   `candidate` / `champion` **aliases** (never deprecated stages), only through a
   deterministic evaluation gate.
3. **Inference integration** — the anomaly-detector resolves a configured alias
   and loads that model version, with an explicit fail-safe when MLflow is down.
4. **Model / data monitoring + drift detection** — reference vs current feature
   distributions, a statistically defensible drift metric, per-feature decisions,
   an overall decision; data drift kept distinct from model-performance
   degradation.
5. **Reproducible retraining workflow** — identify data → features → train →
   evaluate → log → register → compare to champion → promote only on explicit
   criteria. Deterministic; no autonomous deployment.

## 3. Sub-phases

| Sub-phase | Scope | Status |
| --- | --- | --- |
| **6A** | MLflow infrastructure + experiment tracking | **done** |
| **6B** | Model registration + versioning + alias-based promotion gate | **done** |
| **6C** | Inference integration with the registered/aliased model | **done** |
| **6D** | Model / data monitoring + drift detection | **done** |
| **6E** | Reproducible retraining workflow + candidate/champion comparison | **done** |
| **6F** | Documentation, CI integration, end-to-end validation | **done** |

## 4. Sub-phase 6A — MLflow infrastructure + experiment tracking *(done)*

### What exists

- **`ml/mlops/` package** — `config.py` (`MLflowSettings`, `MLFLOW_` prefix:
  tracking URI, registered model name, model alias, `required` flag, experiment
  name) and `tracking.py`:
  - `get_git_sha()` / `get_package_versions()` — reproducibility lineage
    (commit SHA; `mlflow`/`scikit-learn`/`numpy`/`pandas`/`scipy` versions).
  - `setup_experiment(settings)` — point MLflow at the tracking URI, ensure the
    experiment exists, return its id.
  - `log_run(experiment_spec, metrics, artifacts_path, model_path, settings,
    extra_params=…)` — **one MLflow run per trained detector**: experiment/model
    params + hyperparameters, the lineage block, every numeric metric from
    `ml.evaluation`, and the artifacts (report directory, model bundle,
    `experiment_spec.json`). Best-effort: returns `None` and logs a warning if
    MLflow is missing or the store is unreachable — never raises, never
    fabricates a metric.
- **`ml/experiments/runner.py`** — after writing `metrics.json` / `summary.md`
  (unchanged), if `MLFLOW_TRACKING_URI` is set in the environment it mirrors each
  model's run into MLflow via `log_run`. Opt-in by design, so the offline
  pipeline, CI, and the reproducibility tests have no MLflow side effects by
  default.
- **Docker Compose** — `mlflow-init` (one-shot: create the `mlflow` database on
  the existing Postgres if missing) and `mlflow` (tracking + registry server,
  `docker/mlflow/Dockerfile`, `http://localhost:5000`, Postgres backend store,
  `mlflow-artifacts` volume served over HTTP). Nothing in Phases 1–5 depends on
  these; the existing chain starts unchanged.
- **Config / ignores** — `.env.example` documents the `MLFLOW_` variables;
  `.gitignore` excludes `mlruns/`, `mlartifacts/`, and local `*.db` files.

### Local usage

```bash
pip install -e ".[dev,ml,incident,detector,rca,remediation,mlops]"

# Option A — offline / no server (also how tests run):
export MLFLOW_TRACKING_URI="sqlite:///mlruns/mlflow.db"
python -m ml.experiments run exp3_comparison_sentinelops
mlflow ui --backend-store-uri "sqlite:///mlruns/mlflow.db"   # optional, needs full mlflow

# Option B — the shared local server:
docker compose up -d --build mlflow          # brings up postgres + mlflow-init too
export MLFLOW_TRACKING_URI="http://localhost:5000"
python -m ml.experiments run exp3_comparison_sentinelops
# open http://localhost:5000
```

With `MLFLOW_TRACKING_URI` unset, `python -m ml.experiments run …` behaves
exactly as in Phase 2.

### What 6A deliberately did NOT do

- No model registration or aliases (added in 6B); no inference wiring (6C).
- NAB (Track B) experiments are still not MLflow-tracked.
- No drift detection (6D) or retraining workflow (6E).

## 5. Sub-phase 6B — model registry + alias-based promotion *(done)*

### What exists

- **`ml/mlops/registry.py`** — MLflow Model Registry access for the registered
  model `sentinelops-anomaly-detector`:
  - `register_model(model_path, run_id, settings)` — register the bundle logged
    under a run (`source = runs:/<run_id>/model`) as a new **model version**;
    the version links back to its run for lineage.
  - `resolve_alias(settings, alias=None)` → `(version, run_id, model_uri)` —
    `model_uri` is `models:/<name>@<alias>`, what 6C will load.
  - `set_alias` / `delete_alias` / `add_version_tag`.
  - `get_champion_metrics(settings)` → the champion's evaluation metrics, or
    `None` if there is no champion.
  - `get_model_lineage(settings, version)` → run id, params, metrics, tags,
    aliases, artifact URIs.
  - `list_model_versions(settings)`.
- **Aliases** ([ADR-032](../decisions/adr-032-model-alias-strategy.md)) —
  `candidate` (latest registered, awaiting the gate), `champion` (served by
  inference), `previous-champion` (predecessor, for rollback). **No MLflow
  stages** (`Staging` / `Production`) anywhere.
- **`ml/mlops/promotion.py`** — the deterministic gate
  ([ADR-033](../decisions/adr-033-model-promotion-criteria.md)):
  - `PromotionPolicy` (frozen dataclass) — `min_f1=0.75`, `min_recall=0.90`,
    `min_pr_auc=0.60`, `f1_regression_tolerance=0.05`, `require_all_metrics=True`.
    Defaults grounded in the committed Phase 2 numbers (IF exp2 F1 0.82 / recall
    1.00; exp4 F1 0.86).
  - `evaluate_candidate(candidate_metrics, champion_metrics, policy)` →
    `PromotionDecision(promote, reasons, …)`. Pure, deterministic, no LLM, no
    network. Completeness + absolute floors always apply; the F1-regression check
    applies only against an existing champion.
  - `promote_model(settings, candidate_version, reason=…)` — moves
    `previous-champion` → old champion, then `candidate` + `champion` → the new
    version, and records the reason + prior champion as version tags.
- **`python -m ml.mlops` CLI** — `register`, `promote` (runs the gate; exits
  non-zero and leaves the champion untouched on rejection), `list-models`,
  `get-champion`.

### What 6B deliberately does NOT do

- No automatic registration from `run_experiment` — registration and promotion
  are explicit CLI steps (6E automates the retraining loop around this gate).

## 6. Sub-phase 6C — inference integration *(done)*

### What exists

- **`ml/inference/detector_service.py`** —
  `DetectorService.from_registry(settings)`: resolve `settings.model_alias` in
  the registry, `mlflow.artifacts.download_artifacts` the model version's bundle,
  and load it with the existing `AnomalyDetector.load` (no `mlflow.pyfunc` /
  `mlflow.sklearn` — the artifact is always a bundle this project produced). New
  read-only properties: `source` (`"local"` / `"registry"` / `"local-fallback"`),
  `model_version` (the registry version number when loaded from the registry,
  else the model's metadata version), `source_details`. `__init__` and `load()`
  gained optional `model_version` / `source` keyword args — existing callers are
  unaffected.
- **`services/anomaly-detector/anomaly_detector/config.py`** — `DetectorSettings`
  gains `mlflow: MLflowSettings | None` (populated by `DetectorSettings.from_env`
  only when `MLFLOW_TRACKING_URI` is set; `Settings.detector` uses that factory).
- **`services/anomaly-detector/anomaly_detector/training.py`** —
  `ensure_detector(model_path, *, seed, mlflow_settings=None)` keeps its old
  behaviour and, when `mlflow_settings.tracking_uri` is set, delegates to
  `ensure_detector_from_registry`. That helper: **hard-fails** (re-raises) when
  `mlflow_settings.required` is `True`; otherwise logs a warning and returns the
  local bundle tagged `source="local-fallback"`. `get_detector_source(detector)`
  is the `/ready` / `/model-info` summary.
- **`services/anomaly-detector/anomaly_detector/app.py`** — the startup lifespan
  loads the detector **once** (cached; no per-request reload) via `ensure_detector`
  with `settings.detector.mlflow`, stashes it on `app.state.detector`. `/ready`
  now returns `model_loaded` / `model_source` / `model_version` / `model_type`
  alongside `status`; a new `/model-info` returns those plus `source_details`.
- **Docker** — the anomaly-detector image installs the `mlops` extra;
  `docker-compose.yml` passes `MLFLOW_TRACKING_URI` / `MLFLOW_REQUIRED` /
  `MLFLOW_MODEL_ALIAS` / `MLFLOW_REGISTERED_MODEL_NAME` through to the service
  (empty by default → unchanged local behaviour; set
  `MLFLOW_TRACKING_URI=http://mlflow:5000` to use the registry).
- **`ml/mlops/config.py::make_console_emoji_safe()`** — MLflow writes emoji to
  stdout on run-end / model registration against an HTTP tracking server, which
  raises `UnicodeEncodeError` on a Windows `cp1252` console and would silently
  defeat `log_run` / the CLI. The helper (called from `log_run`, `_get_client`,
  `register_model`, the CLI) makes stdout/stderr drop unencodable characters
  instead. No effect on Linux/CI or on the container inference path.

### Fail-safe matrix

| `MLFLOW_TRACKING_URI` | registry reachable | `MLFLOW_REQUIRED` | result |
| --- | --- | --- | --- |
| unset | — | — | local bundle (`source=local`) — exactly as Phases 3–5 |
| set | yes, `champion` exists | either | registry model (`source=registry`, `model_version=<n>`) |
| set | no / no champion | `false` | local bundle, warning logged (`source=local-fallback`) |
| set | no / no champion | `true` | startup error → `/ready` 503 |

### What 6C deliberately does NOT do

- Does not auto-register or promote at inference time — the service only *reads*
  the `champion` alias.

## 7. Sub-phase 6D — model / data monitoring + drift detection *(done)*

### What exists

- **`ml/monitoring/baseline.py`** — `BaselineDistribution` (feature names,
  per-feature quantile `bin_edges`, `reference_proportions`, `statistics`
  mean/std/min/max/p05–p95, `n_samples`, `model_version`,
  `feature_schema_version`). `freeze_baseline(x, feature_names, model_version,
  feature_schema_version)` builds it from **training features only** (10 quantile
  bins by default; a near-constant feature collapses to one bin).
  `save_baseline` / `load_baseline` (joblib).
- **`ml/monitoring/drift.py`** —
  - `calculate_psi(expected, actual)` = `Σ (aᵢ − eᵢ)·ln(aᵢ/eᵢ)`; zeros floored to
    `1e-6`.
  - `classify_psi(psi, thresholds=(0.1, 0.25))` → `none` / `moderate` /
    `significant`.
  - `compute_prediction_drift(prev, current)` = `(current − prev)/prev`, or
    `None` when `prev <= 0`.
  - `detect_drift(x_current, baseline, model_version=…, psi_thresholds=…,
    prediction_rate_previous=…, prediction_rate_current=…)` → `DriftReport`
    (`feature_reports` [per-feature PSI + classification + decision +
    reference/current stats], `overall_decision` = most severe,
    `prediction_drift` in a **separate field**, `missing_features`, `timestamp`,
    `model_version`, `n_samples_current`). Current values are clipped into the
    reference range; features missing from `x_current` are reported and skipped.
- **`python -m ml.monitoring`** — `baseline --model-version <v> --data <csv>
  --output <path>` and `check --baseline <path> --data <csv> [--output <json>]`
  (rebuilds features with the Phase 2 pipeline; `check` exits non-zero on
  `significant_drift`).
- **Storage** — `ml.mlops.tracking.log_run(..., baseline=…)` logs the baseline to
  the run at `baseline/baseline.json`; `ml.mlops.registry.set_model_baseline` /
  `get_model_baseline` associate it with a model version;
  `ml.experiments.runner.run_experiment` freezes one baseline per experiment from
  the training features (saved next to the models, logged with each run);
  `promote_model(..., baseline=…)` / `_store_baseline` attach it to the champion,
  and a champion with no baseline is warned about (promotion still succeeds).
- `FEATURE_SCHEMA_VERSION` added to `ml/data/schema.py`.

Methodology, bands, alternatives, and limitations: **ADR-034**.

### Real drift example (`run_a` baseline vs `run_b`)

`run_b` swaps `run_a`'s latency/error faults for publish-failure/surge, and the
detector picks that up feature-by-feature:

| feature | PSI | classification |
| --- | --- | --- |
| `latency_mean_ms` | 2.61 | significant |
| `publish_rate` | 1.31 | significant |
| `orders_created_rate` | 0.12 | moderate |
| `error_rate` / `success_rate` | 0.006 | none |
| overall | — | **significant_drift** |

### What 6D deliberately does NOT do

- No label-based performance monitoring — drift ≠ degradation (ADR-034).
- No online/streaming drift in the anomaly-detector service; drift checks are a
  batch CLI / library call over a collected window.

## 8. Sub-phase 6E — reproducible retraining workflow *(done)*

### What exists

- **`ml/mlops/retraining.py`** — `retrain_pipeline(config)` runs the **existing**
  Phase 2 pipeline end to end and threads it through the lifecycle. It reuses,
  not reimplements: `ml.data.prepare.load_processed_run`, `ml.splits`,
  `ml.features.engineering.build_features`, `ml.models.*`,
  `ml.evaluation.metrics.evaluate`, `ml.monitoring.freeze_baseline` (6D),
  `ml.mlops.tracking.log_run` (6A), `ml.mlops.registry.register_model` (6B),
  `ml.mlops.promotion.evaluate_candidate` + `promote_model` (6B).
  - `RetrainingConfig(dataset_id, seed=42, model_type="isolation_forest",
    promote_if_passing=False, policy=None)` — closed `model_type` set; a bad type
    fails at construction.
  - `load_dataset` / `train_model` / `evaluate_model` — the individual steps,
    exported for reuse; threshold is always calibrated on the **validation**
    split.
  - Steps: load → split → features → train → calibrate(val) → evaluate(test) →
    freeze baseline(train) → `log_run(..., baseline=…)` → `register_model` →
    `set_model_baseline` → `get_champion_metrics` → **`evaluate_candidate`** vs
    champion → promote **only if** `promote_if_passing` **and** the gate passes.
  - `RetrainingResult(candidate_version, promotion_decision, metrics, run_id,
    champion_version, promoted, baseline_path)`.
  - **Deterministic** — same `dataset_id` + `seed` → identical metrics (tested);
    each run still registers a *new* version.
- **`python -m ml.mlops retrain --dataset <id> [--seed N] [--model-type X]
  [--promote-if-passing]`** — prints step-by-step progress + the gate decision,
  exits `0` when the gate passes, `1` when it rejects, `2` on a setup error
  (e.g. MLflow unreachable). **MLflow is required** for `retrain` (you cannot
  register a version without it).
- **`scripts/retraining_demo.py`** — deterministic `run_a` seed-42 retrain →
  track → register → gate (no promotion).
- **`scripts/drift_triggered_retraining.py`** — resolve champion + baseline →
  `detect_drift` on `run_b` (stand-in production) → if `significant_drift`,
  retrain on `run_b` and run the gate. Bootstraps a champion if none exists.
  Still **CLI-driven — nothing auto-deploys**.
- **Makefile** — `mlops-retrain`, `mlops-retrain-promote`, `mlops-retrain-demo`,
  `mlops-drift-retrain`, `docker-build-mlflow`.

### Example (`python -m ml.mlops retrain --dataset run_a --seed 42`)

```
=== Retraining: dataset=run_a, seed=42, model=isolation_forest ===
Loaded dataset run_a... 144 windows, 23 features
Trained isolation_forest
Calibrated threshold on validation... objective=f1, threshold=0.4881
Evaluated on test... F1=0.818 recall=1.000 PR-AUC=0.700
Logged to MLflow... run_id=f4c7517f...
Registered model... version=1
Gate: PASS vs champion (no champion)
DECISION: PASS
  - F1 0.8182 / recall 1.0000 / PR-AUC 0.6997 meet all floors
  - no prior champion - first model
Candidate v1 passed all criteria
```

### What 6E deliberately does NOT do

- **No autonomous deployment / promotion** — `--promote-if-passing` is opt-in and
  still goes through `evaluate_candidate`; the drift script never auto-promotes.
- **No scheduler / Airflow / cron** — retraining is a manual CLI/`make` call.
- Does not reimplement any Phase 2 code — it is orchestration only.

## 9. Sub-phase 6F — documentation, CI integration, end-to-end validation *(done)*

### What exists

- **`scripts/phase6_e2e_demo.py`** — one deterministic run of the whole
  lifecycle: train champion (`run_a`) → register + promote → load the champion
  from the registry → drift-check on `run_b` → retrain on `run_b` → gate → promote.
  Needs no server (throwaway sqlite store by default); honours
  `MLFLOW_TRACKING_URI` when set. `make phase6-demo`.
- **`tests/ml/test_phase6_e2e.py`** — runs that demo end to end (subprocess,
  `@pytest.mark.mlflow`) and asserts the flow and the numbers.
- **CI** — a new `mlflow-integration` job (`needs: [quality, ml]`,
  `continue-on-error: true`) runs `pytest -m mlflow` and the e2e demo against a
  **PostgreSQL-backed** MLflow store; the `ml` job already covers the sqlite path
  for every Phase 6 test. The `mlflow` pytest marker is registered.
- **Docs** — `docs/phase6-summary.md` (reviewer one-pager with the Mermaid flow +
  real numbers); `README.md`, `docs/phases/roadmap.md`,
  `docs/architecture/overview.md`, `docs/development/setup.md`,
  `docs/decisions/README.md` and `.env.example` updated to *complete*.

### End-to-end result (`scripts/phase6_e2e_demo.py`, seed 42)

```
1. Training champion on run_a...      -> Champion v1: F1=0.818, Recall=1.000, PR-AUC=0.700
2. Loading champion via registry...   -> model_source: registry, model_version: 1
3. Detecting drift on run_b...        -> significant_drift (n=108); 11 significant, 4 moderate, 8 none
4. Retraining on run_b...             -> Candidate v2: F1=0.889, Recall=1.000, PR-AUC=0.688
5. Evaluating candidate vs champion...-> F1 change +0.071; all floors PASS; gate: PASS
6. Promoting candidate...             -> previous-champion -> v1, candidate -> v2, champion -> v2
PASS - Phase 6 end-to-end flow verified.
```

## 10. Phase 6 exit criteria

| Criterion | Status |
| --- | --- |
| MLflow integrated into the ML workflow; runs record real params/metrics/artifacts/lineage | ✅ 6A |
| Models registered; **aliases** used, not stages; champion/candidate semantics documented | ✅ 6B (ADR-032) |
| Deterministic promotion gate; a candidate cannot replace the champion just because training succeeded | ✅ 6B (ADR-033) |
| Feature/schema compatibility checked; reproducibility metadata recorded | ✅ 6A/6E (`FEATURE_SCHEMA_VERSION`, lineage block) |
| A baseline distribution can be associated with a model version | ✅ 6D |
| Data/feature drift detection works; no-drift and drift scenarios tested | ✅ 6D |
| Monitoring separates drift from label-dependent performance degradation | ✅ 6D (ADR-034) |
| Controlled retraining workflow; retrained models pass through evaluation before promotion; failed candidates don't replace the champion | ✅ 6E |
| Existing anomaly detection / inference remains functional; Phase 0–5 tests still pass | ✅ (1016 passed) |
| Ruff, Ruff format, mypy pass; Docker Compose valid; MLflow local setup documented | ✅ |
| Reproducible end-to-end demo; no secrets; no fabricated metrics; Phase 7/8 not implemented | ✅ |

## 11. Design decisions

| Decision | ADR |
| --- | --- |
| MLflow for experiment tracking + registry; local Compose deployment; `mlflow-skinny` client; additive fail-safe tracking | [ADR-031](../decisions/adr-031-mlflow-tracking-and-registry.md) |
| Model alias strategy (`candidate` / `champion` / `previous-champion`, not stages) | [ADR-032](../decisions/adr-032-model-alias-strategy.md) |
| Deterministic model promotion criteria | [ADR-033](../decisions/adr-033-model-promotion-criteria.md) |
| 6C inference: registry alias resolution + explicit local fallback | [ADR-032](../decisions/adr-032-model-alias-strategy.md) (§ Consequences) |
| Data-drift detection via PSI against a frozen training baseline | [ADR-034](../decisions/adr-034-drift-detection-methodology.md) |
| 6E retraining: orchestration only — reuses ADR-031/032/033/034, no new decision | — |

## 12. Dependencies added

| Package | Where | Why |
| --- | --- | --- |
| `mlflow-skinny>=2.19,<2.23` | `mlops` optional group; also in the anomaly-detector image (`.[ml,detector,mlops]`, 6C) | tracking + registry client; does not downgrade pandas/numpy |
| `psycopg2-binary==2.9.12` | `docker/mlflow/Dockerfile` only | PostgreSQL driver for the MLflow server's backend store |
| _(none for 6D / 6E)_ | — | drift detection + retraining use only `numpy` / `pandas` / `joblib` / the existing Phase 2 code, already in the `ml` extra |
