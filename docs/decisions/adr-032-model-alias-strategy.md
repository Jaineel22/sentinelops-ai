# ADR-032: Model promotion uses MLflow aliases, not stages

- Status: Accepted
- Date: 2026-09-03

## Context

MLflow offers two ways to mark "which version is live":

- **Stages** (`None` / `Staging` / `Production` / `Archived`) — the original
  mechanism. A version has exactly one stage; transitions are
  `transition_model_version_stage`. **Deprecated** since MLflow 2.9 and slated
  for removal.
- **Aliases** — named pointers (`champion`, `candidate`, …) set with
  `set_registered_model_alias`. A version can carry several; an alias can be
  re-pointed atomically; consumers load `models:/<name>@<alias>`.

SentinelOps needs *candidate vs champion* semantics, the ability to keep a
pointer to the outgoing model for rollback, and a resolution mechanism the
anomaly-detector can use at startup (Sub-phase 6C). Stages model none of this
cleanly and are on the way out.

## Decision

Use **MLflow model aliases** for the registered model
`sentinelops-anomaly-detector`. Three aliases, defined in `ml/mlops/registry.py`:

| Alias | Meaning |
| --- | --- |
| `candidate` | the most recently registered version, awaiting the promotion gate |
| `champion` | the version inference serves — the only one Sub-phase 6C resolves |
| `previous-champion` | the immediately preceding champion, kept for rollback |

`ml.mlops.promotion.promote_model` performs an atomic-ish sequence: point
`previous-champion` at the outgoing champion (if any and different), then
`candidate` and `champion` at the newly promoted version. The reason and the
prior champion are recorded as model-version tags.

MLflow **stages are never used** — no code calls
`transition_model_version_stage`.

## Alternatives considered

- **Stages (`Staging`/`Production`).** Deprecated; single-label; no rollback
  pointer; MLflow's own docs steer new work to aliases.
- **A single `champion` alias only.** Simpler, but loses the audit/rollback
  pointer and the "this version is the pending candidate" signal the CLI and
  retraining workflow (6E) rely on.
- **Tracking the live version in our own config / a DB table.** Reinvents what
  the registry already does and splits the source of truth.
- **Git-tagging model artifacts.** Artifacts are git-ignored; the registry is
  the artifact system of record.

## Consequences

- Promotion goes through `set_registered_model_alias` / the
  `MlflowClient` alias APIs; consumers resolve `models:/<name>@champion`.
- Requires MLflow ≥ 2.9 (we run 2.22) and a database-backed registry store
  (Postgres in Compose, sqlite for tests) — a file store cannot hold aliases.
- Phases 0–5 and Sub-phases 6A–6B still load the detector from a local file
  path; Sub-phase 6C switches the anomaly-detector to alias resolution with an
  explicit local fallback.
- The alias set is closed and code-defined; nothing lets an operator invent a
  new promotion label that inference would then trust.
