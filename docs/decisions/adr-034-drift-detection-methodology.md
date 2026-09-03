# ADR-034: Data-drift detection via PSI against a frozen training baseline

- Status: Accepted
- Date: 2026-09-03

## Context

The anomaly detector is trained offline on a fixed feature distribution (Phase 2)
and served through the registry (Phase 6C). Nothing yet notices when the
**production feature distribution drifts** away from what the champion was
trained on — the first sign that the model may be operating outside its
competence. Phase 6D adds that check. It must be:

- **label-free** — production windows have no ground truth; drift detection
  cannot wait for labels;
- **deterministic and explainable** — same inputs → same PSI → same decision, no
  LLM ([ADR-002](adr-002-ml-and-llm-separation.md));
- **statistically defensible** with accepted thresholds — not a hand-tuned score;
- **leak-free** — the reference is the *training* distribution, frozen once, never
  updated with production or labelled data.

Phase 2 features are 23 continuous signals (`ml/data/schema.py::FEATURE_COLUMNS`);
there are no categorical features. Datasets are small (`run_a` 144 windows; a
production monitoring window would be a few hours of 10 s telemetry ≈ hundreds of
rows).

## Decision

**Population Stability Index (PSI) per feature, against a frozen quantile-binned
baseline.**

- **Baseline** (`ml/monitoring/baseline.py::freeze_baseline`) — per feature:
  quantile bin edges (default **10 bins ≈ deciles** of the training values,
  de-duplicated; a near-constant feature collapses to one bin), the reference
  proportion in each bin, and summary statistics (mean/std/min/max/p05/p25/p50/
  p75/p95). Tagged with the model version and `FEATURE_SCHEMA_VERSION`.
  Stored as `baseline/baseline.json` on the model version's source run
  (`ml/mlops/registry.py::set_model_baseline` / `get_model_baseline`), logged at
  training time by `ml.mlops.tracking.log_run` and associated with the champion
  at promotion.
- **PSI** (`ml/monitoring/drift.py::calculate_psi`) —
  `Σ (actualᵢ − expectedᵢ) · ln(actualᵢ / expectedᵢ)`. Current values are clipped
  into the reference range so out-of-range production values fold into the end
  bins; zero proportions are floored to `1e-6` before the log (not renormalised —
  PSI is a heuristic index).
- **Bands** (`classify_psi`, the widely-cited Siddiqi thresholds):

  | PSI | classification | per-feature decision | interpretation |
  | --- | --- | --- | --- |
  | `< 0.10` | `none` | `pass` | stable |
  | `0.10 – 0.25` | `moderate` | `warn` | monitor; consider retraining |
  | `≥ 0.25` | `significant` | `fail` | retrain |

  Thresholds are the default `DEFAULT_PSI_THRESHOLDS = (0.10, 0.25)` and are
  overridable per call.
- **Overall decision** — the most severe per-feature classification
  (`no_drift` / `moderate_drift` / `significant_drift`).
- **Prediction drift** — the relative change in anomaly rate
  (`(current − prev) / prev`) is reported in a **separate field**, never merged
  into the feature-drift decision.

## Alternatives considered

- **Kolmogorov–Smirnov two-sample test.** Good for continuous features, but
  returns a p-value that is dominated by sample size (with hundreds of rows even
  trivial shifts become "significant") and does not extend to binned/categorical
  data. PSI gives an effect-size magnitude with fixed, interpretable thresholds.
- **KL divergence.** The same `Σ p·ln(p/q)` family as PSI but asymmetric and
  without accepted decision thresholds; PSI's symmetrised form and its
  `<0.1 / 0.1–0.25 / ≥0.25` convention are the industry standard.
- **Wasserstein / energy distance.** Scale-dependent, no standard threshold,
  needs per-feature normalisation — more knobs, not more signal, at this scale.
- **Equal-width (Sturges) bins.** PSI's bands assume roughly equal-mass reference
  bins; equal-width bins on skewed signals (latency, error rate) put almost all
  reference mass in one bin and make PSI unstable. Quantile bins fix the
  reference mass at ~10% per bin.
- **A drift-detection library (Evidently, Alibi-Detect, NannyML).** Heavier
  dependency surface for one well-understood statistic; PSI in ~60 lines is
  auditable and matches the project's "smallest defensible method" rule.

## Consequences

- Every champion must carry a baseline. `promote_model` stores one when given
  (`baseline=` / `_store_baseline`) or reuses the training-time run baseline; if
  neither exists it logs a warning and drift checks are unavailable until one is
  stored (`python -m ml.monitoring baseline …`).
- Missing features in the production window are reported (`missing_features`) and
  skipped rather than raising; a `FEATURE_SCHEMA_VERSION` mismatch is visible on
  the baseline for the caller to act on.
- **Synthetic ≠ production drift.** The controlled drift scenarios in the tests
  prove the detector *responds* to a distributional shift; they are not a claim
  about real-world drift-detection quality on this small dataset.
- Drift is not degradation. A `significant_drift` result is a trigger to
  *evaluate* (Phase 6E retraining + the promotion gate), not proof the model got
  worse — that still needs labels.
