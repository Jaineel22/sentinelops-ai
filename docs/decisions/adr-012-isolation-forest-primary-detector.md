# ADR-012: Isolation Forest is the primary anomaly detector

- Status: Accepted
- Date: 2026-08-31

## Context

Phase 2 must ship a detector that can later run in the live path (Phase 3+).
Constraints from the platform's reality:

* Labelled incidents are **rare** in a real deployment — we cannot assume a
  large, clean, balanced training set of failure examples.
* Anomalies are **multivariate** — "latency is fine *and* publish rate dropped"
  can be the signal.
* Scoring must be **cheap and deterministic** (one score per telemetry window,
  reproducible in CI).

## Decision

Ship three detectors with a shared interface, in this order of authority:

1. **Baseline — Robust z-score (median / MAD).** Unsupervised, per-feature,
   robust to contaminated training data. The reference every model must beat.
2. **Primary — Isolation Forest** (scikit-learn), fitted on the *normal*
   training windows (semi-supervised novelty detection), preceded by a
   `StandardScaler`. Multivariate, no distributional assumptions, ~ms to fit,
   ~µs to score, deterministic with a fixed `random_state`.
3. **Comparator — Random Forest classifier** (supervised). Included only to
   show the gap: it wins on fault types it was trained on and **loses on a
   held-out fault type** (Experiment 4), which is the argument for shipping the
   unsupervised detector live.

XGBoost / LightGBM is **not** added — Random Forest from scikit-learn is enough
for the comparison and adds no dependency. PyTorch / deep models are not
justified at this data scale (hundreds of windows).

## Alternatives considered

- **Supervised classifier as the primary.** Best numbers on known faults, but
  needs labels we won't have live and generalises poorly to new failure modes.
- **One-Class SVM / LOF.** Reasonable peers to IF; IF chosen for speed,
  determinism, and scikit-learn maturity. Easy to add later behind the same
  interface.
- **Deep time-series models (LSTM-AE, etc.).** Data-hungry; premature.

## Consequences

- The live detector (Phase 3) only needs "known-normal" windows to train — a
  cheap labelling requirement.
- "Semi-supervised" is stated precisely everywhere; we do not claim fully
  unsupervised.
- All three detectors implement `fit / score_samples / predict / save / load`,
  so swapping models later is a one-line change.
