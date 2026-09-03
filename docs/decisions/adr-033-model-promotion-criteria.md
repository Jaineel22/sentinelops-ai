# ADR-033: Deterministic model-promotion criteria

- Status: Accepted
- Date: 2026-09-03

## Context

A retrained model must not become the champion just because training finished.
Phase 6 needs a **gate**: a decision function that says whether a candidate may
take the `champion` alias, with reasons. It must be deterministic, testable, and
free of any LLM — an LLM never decides what runs in production
([ADR-002](adr-002-ml-and-llm-separation.md)). Thresholds must be justifiable
against the numbers Phase 2 actually measured, not invented.

Phase 2's committed results (`artifacts/reports/summary.md`, seed 42), window-wise
on the held-out test set:

| model | F1 | recall | PR-AUC | FPR |
| --- | --- | --- | --- | --- |
| Isolation Forest — exp2 (chronological) | 0.82 | 1.00 | 0.70 | 0.27 |
| Isolation Forest — exp4 (held-out fault) | 0.86 | 1.00 | 0.74 | 0.17 |
| robust z-score baseline — exp2 | 0.71 | 0.67 | 0.82 | 0.13 |

## Decision

`ml.mlops.promotion.evaluate_candidate(candidate_metrics, champion_metrics,
policy)` returns a `PromotionDecision(promote, reasons, …)`. `PromotionPolicy`
(frozen dataclass, every field overridable) with these defaults:

| Check | Default | Rationale |
| --- | --- | --- |
| **Evaluation completeness** | F1, recall, PR-AUC all present (`require_all_metrics=True`) | a candidate with a partial evaluation is not comparable — fail closed |
| **`min_f1`** | `0.75` | below the Isolation Forest's exp2/exp4 F1 (0.82 / 0.86) and clearly above the baseline (0.71); a real regression, not noise |
| **`min_recall`** | `0.90` | the detector's job is to miss as few anomalous windows as possible; the shipped IF holds recall 1.00, so 0.90 is a deliberate floor with headroom |
| **`min_pr_auc`** | `0.60` | below the IF's measured 0.70 / 0.74; guards against a model that only looks good at one operating point |
| **`f1_regression_tolerance`** | `0.05` | vs an existing champion, F1 may not drop by more than this; ~one confidence-interval width on this small test set (phase-2.md §14: "a couple of windows swing F1 by ~0.05") |

Rules: completeness and the absolute floors **always** apply. The regression
check applies **only** when a champion already exists — the first model is not
compared against a non-existent predecessor, but it must still clear the floors.
A candidate that is strictly better, or equivalent within tolerance, passes;
anything worse is rejected with a reason and the champion alias is left
untouched.

The CLI (`python -m ml.mlops promote --candidate-version <v>`) runs this gate and
exits non-zero on rejection. The gate reads only numeric metrics logged by
`ml.mlops.tracking` from real evaluation runs — no free-text field, no model
artifact introspection, no network judgement.

## Alternatives considered

- **Promote on training success.** Rejected outright — the whole point of the
  gate.
- **"Beat the champion on F1, full stop."** No absolute floor means a slow drift
  downward is possible as long as each step is an improvement on a already-bad
  predecessor; and a first model with no champion would face no bar at all.
- **A single composite score.** Hides *why* a model failed; harder to explain in
  review and to act on.
- **Statistical significance testing between candidate and champion.** The test
  set is ~48 windows (phase-2.md §14) — significance testing at that n is not
  meaningful. A documented tolerance is more honest.
- **Tuning the thresholds automatically.** That is an optimisation loop with its
  own failure modes; fixed, documented, overridable values are the right call
  for Phase 6.

## Consequences

- Promotion is reproducible: same metrics in → same decision out.
- Thresholds live in one dataclass and can be overridden per call / per
  environment without touching the logic.
- A candidate that regresses stays `candidate`; the operator sees the reasons.
  Sub-phase 6E (retraining) reuses this exact gate before touching any alias.
- If Phase 2's methodology or dataset changes materially, the defaults here must
  be revisited against the new committed numbers — the ADR is the record of why
  they are what they are.
