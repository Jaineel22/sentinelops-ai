# Phase 2 — ML Anomaly Detection + Offline Evaluation

> Status: implemented. All numbers in this document come from
> `artifacts/reports/` and are regenerable with `make ml-experiments`.
> Regenerate the summary table below with the values in
> `artifacts/reports/summary.md` after any change.

## 1. Problem definition

Given a stream of operational telemetry from a service, decide **per time
window** whether the service is behaving normally or anomalously, early enough
and precisely enough to be useful to an on-call engineer — without assuming a
large catalogue of labelled past incidents.

## 2. Why anomaly detection

Static threshold alerts are brittle: they need a human to pick and maintain a
number per metric, they miss multivariate problems ("latency fine, publish rate
halved"), and they are noisy at the edges. SentinelOps needs a detector that
learns the *shape* of normal from data and flags departures from it. Phase 2
builds that detector and, crucially, **measures whether it actually works** —
including on a failure mode it was never trained on, and on a completely
independent public dataset.

Machine learning here means **detecting anomalies** — not an LLM
([ADR-002](../decisions/adr-002-ml-and-llm-separation.md)). The LLM agent
(Phase 4) reasons about incidents *after* this detector and Phase 3 have flagged
and correlated them.

## 3. Data sources

Two tracks, kept strictly separate
([ADR-004](../decisions/adr-004-datasets-vs-live-telemetry.md)):

| | Track A | Track B |
| --- | --- | --- |
| Source | Phase 1 `orders-service` `/metrics` under scripted load | Numenta Anomaly Benchmark (`github.com/numenta/NAB`) |
| Nature | Multivariate microservice telemetry (our feature space) | Univariate real-world sensor / cloud metrics |
| Role | Train + evaluate the detector we intend to ship | **Methodology check only** — never trains a model used on Track A |
| In repo | Processed window CSVs committed; raw scrapes git-ignored | Nothing committed — downloaded + sha256-pinned ([ADR-013](../decisions/adr-013-nab-benchmark-track.md)) |

## 4. Track A — the SentinelOps telemetry dataset

### 4.1 Collection ([ADR-011](../decisions/adr-011-ml-dataset-via-metrics-scraping.md))

`ml/collection/collector.py` drives a **deterministic, interleaved** sequence of
scenario segments against a running `orders-service` and scrapes `GET /metrics`
every 10 s, writing cumulative counter/histogram values plus the active
scenario. The `main` plan is three identical cycles of
`[normal, latency, normal, errors, normal, surge, normal, recovery]` — faults
are evenly spaced so a later chronological split gives every part a
proportional share of anomalies, and `normal` outnumbers faults so the dataset
is realistically imbalanced (~35% anomalous).

Scenarios (mirror `scripts/generate_traffic.py`; a test enforces parity):

| scenario | injection | ground-truth label | binary |
| --- | --- | --- | --- |
| `normal` | none | `normal` | 0 |
| `recovery` | none (injection cleared) | `recovery` | 0 |
| `latency` | +400 ms request latency | `latency_anomaly` | 1 |
| `errors` | 25% of requests → 500 | `error_anomaly` | 1 |
| `publish_failure` | 25% of Kafka publishes fail → 503 | `publish_failure` | 1 |
| `surge` | ~4× request rate, no fault | `traffic_surge` | 1 |

**`traffic_surge` is labelled anomalous.** A sudden ~4× load change is an unusual
operating condition an operator would want surfaced, even though nothing has
"failed". This is a judgement call; it is revisited if it hurts more than helps.

Two canonical runs, both committed as small CSVs:

| run | plan | fault types | used for |
| --- | --- | --- | --- |
| `run_a` | `main` (3 cycles) | latency, error, surge (+ normal, recovery) | Experiments 1-3, and the *training* half of Experiment 4 |
| `run_b` | `holdout` (3 cycles) | publish-failure, surge (+ normal, recovery) | the *test* half of Experiment 4 |

Both were collected against the **same** `orders-service` process so their
"normal" distributions align (an earlier attempt with two processes showed a
distribution shift that inflated the held-out false-positive rate).

### 4.2 Raw → windows (`ml/data/prepare.py`)

A **window** = the interval between two consecutive scrapes within one scenario
segment. Per window:

* rates = counter delta ÷ elapsed seconds
* `error_rate` / `success_rate` = 5xx / 2xx share of requests
* `latency_mean_ms` = latency-sum delta ÷ request delta
* `latency_p50/p90/p95_ms` = interpolated from histogram-bucket deltas
* `publish_latency_mean_ms` = publish-latency-sum delta ÷ publish-count delta
  (percentiles unavailable at the current histogram bucket config — mean only)

Windows spanning a **scenario boundary** or a **counter reset** (process
restart) are dropped, not miscounted.

### 4.3 Data quality (`ml/data/validation.py`)

The pipeline raises `DataValidationError` — it never trains on bad data — for:
missing required columns, unparseable / duplicate / unsorted timestamps, missing
values, infinite values, out-of-range values (rates < 0, error/success rate
outside [0, 1], negative latency), and too-few rows.

### 4.4 Labels vs features

Ground-truth labels live in a **separate** `labels.csv` (`run_id`,
`window_start`, `window_end`, `scenario`, `label`, `is_anomaly`) and are joined
only for evaluation. The failure-injection counters
(`orders_failure_injection_total`) and the scenario name are recorded in raw
snapshots for debugging but are **excluded from features by an allow-list**
(`FEATURE_COLUMNS`) whose complement is checked in `tests/ml/test_features.py`.
The model observes system behaviour, never the injected answer.

## 5. Feature engineering (`ml/features/engineering.py`)

One implementation, used by **both** training (whole frame) and inference
(trailing buffer) — `tests/ml/test_features.py` asserts they agree row-for-row.
All engineered features are **causal**: a feature for window *t* uses only
windows ≤ *t* (trailing rolling windows, backward differences, lag-1), computed
**per run** so statistics never cross collection runs. This is what makes the
chronological split leak-free.

| feature | represents | why it matters | how computed | real-time? | leakage risk |
| --- | --- | --- | --- | --- | --- |
| `request_rate` | POST /orders throughput | load level | Δreq / Δt | yes | none |
| `error_rate` | 5xx share | failing requests | Δ5xx / Δtotal | yes | none |
| `success_rate` | 2xx share | inverse health | Δ2xx / Δtotal | yes | none |
| `latency_mean_ms` | mean request latency | slowness | Δlatsum / Δreq | yes | none |
| `latency_p50/p90/p95_ms` | latency distribution tail | tail slowness | bucket-delta interpolation | yes | none |
| `publish_rate` | Kafka publish attempts/s | downstream load | Δpub / Δt | yes | none |
| `publish_error_rate` | failed publishes share | backbone health | Δpubfail / Δpub | yes | none |
| `publish_latency_mean_ms` | mean publish latency | backbone slowness | Δpublatsum / Δpubcount | yes | none |
| `orders_created_rate` | successful orders/s | business throughput | Δcreated / Δt | yes | none |
| `*_roll_mean` / `*_roll_std` (k=3) | recent level & volatility of a signal | "is *now* unlike the last ~30 s" | trailing window incl. current | yes (needs 2 prior windows) | causal — none |
| `*_delta` | change since previous window | sudden shifts | value − value.shift(1) | yes | causal — none |
| `traffic_growth_rate` | fractional change in request rate | detects surges / drops | (rate − rate₋₁) / max(rate₋₁, 1) | yes | causal — none |

23 features total (11 signals + 12 engineered).

## 6. Labeling (recap)

Ground truth is the scenario the collector set for the window's interval. A
window is the gap between two consecutive scrapes; if those two scrapes are in
different scenario segments the window is **dropped**, so every labelled window
sits cleanly inside exactly one scenario. Binary `is_anomaly` per
[§4.1](#41-collection-adr-011).

## 7. Train / validation / test strategy (`ml/splits.py`)

* **Chronological** (Experiments 1-3): sort `run_a` windows by time, take the
  earliest 50% as train, next 17% as validation, latest 33% as test. Because the
  plan is three identical cycles, the test third is exactly the **final cycle** —
  it contains every fault type, and train/val have seen each fault type earlier
  in time. Asserted: no timestamp overlap across boundaries. **No random split**
  — that would let the model see the future.
* **Held-out fault** (Experiment 4): train/validate on `run_a` windows whose
  label ∈ {normal, recovery, latency_anomaly, error_anomaly}; test on `run_b`
  windows whose label ∈ {normal, recovery, publish_failure, traffic_surge}.
  Asserted: the held-out fault labels never appear in train/val.
* **NAB** (Experiments 5-6): each series split per-series, chronological
  20/15/65 — a short early "probationary" train slice to learn normal, a small
  validation slice for the threshold, and the long tail as test (where NAB's
  labelled anomalies mostly fall). Threshold calibrated to a 5% FPR budget.
  Metrics are macro-averaged across the family's series.

The threshold that turns a score into a 0/1 decision is **always calibrated on
validation**, never on test.

## 8. Baseline — robust z-score (`ml/models/baseline.py`)

Per-feature median and MAD (median absolute deviation) fitted on the **normal**
training windows; a window's score is the largest robust z across features
(`|x − median| / (1.4826·MAD)`). Chosen because it is unsupervised, needs no
Gaussian assumption, and a few contaminated training windows barely move a
median. Constant features (MAD = 0) contribute z = 0.

## 9. Isolation Forest — primary (`ml/models/isolation_forest.py`)

`sklearn.ensemble.IsolationForest` (200 trees, fixed `random_state`) behind a
`StandardScaler`, fitted on the **normal** training windows (semi-supervised
novelty detection). scikit-learn's `score_samples` is higher-for-normal; we
negate it so higher = more anomalous, consistent with the baseline.
Rationale: [ADR-012](../decisions/adr-012-isolation-forest-primary-detector.md).

## 10. Supervised comparator — Random Forest (`ml/models/supervised.py`)

`RandomForestClassifier` trained on **labelled** train windows. Present only to
quantify the gap: it should win on fault types it saw and lose on the held-out
fault type. Not a shippable live detector (needs labels we won't have). No new
dependency (scikit-learn); XGBoost/LightGBM deferred.

## 11. Evaluation metrics (`ml/evaluation/metrics.py`)

**Primary framing: window-wise (point-wise on the windowed series).** Each ~10 s
window is one labelled example.

| metric | why |
| --- | --- |
| precision | of flagged windows, how many were real — alert fatigue |
| recall / anomaly coverage | of anomalous windows, how many caught |
| F1 | headline single number under class imbalance |
| false positive rate | false-alarm budget |
| false negative rate | missed anomalies (= 1 − recall) |
| PR-AUC (average precision) | threshold-free, imbalance-robust score quality |
| confusion matrix | the raw tp/fp/fn/tn |

Accuracy is reported but never headlined (an "always normal" model scores
0.65-0.8 at our anomaly fractions).

**Secondary framing: event-wise.** Contiguous anomalous windows are grouped into
*events*; we report event recall (did we flag the event at all), mean/max
**detection delay** in windows and seconds, and **false alarms per hour** of
normal operation.

## 12. Experiments

| # | name | data | question |
| --- | --- | --- | --- |
| 1 | `exp1_baseline_sentinelops` | Track A chronological | how far does a statistical baseline get? |
| 2 | `exp2_isolation_forest_sentinelops` | Track A chronological | how does the primary detector do? |
| 3 | `exp3_comparison_sentinelops` | Track A chronological | baseline vs IF vs supervised RF, same split |
| 4 | `exp4_heldout_fault_sentinelops` | Track A held-out fault | can it flag `publish_failure` / `surge`, unseen in training? |
| 5 | `exp5_nab_realknowncause` | NAB realKnownCause | does the methodology work on real-world data? |
| 6 | `exp6_nab_realawscloudwatch` | NAB realAWSCloudwatch | second independent NAB family |

Every experiment records dataset, feature set, model + hyperparameters, split
sizes, seed, git SHA, Python version, and the full metric set to
`artifacts/reports/<name>/metrics.json`.

## 13. Results

All numbers below are from `artifacts/reports/` (seed 42). Regenerate with
`make ml-experiments`; the canonical dataset is `run_a` (144 windows, 37.5%
anomalous) and `run_b` (108 windows, 33% anomalous).

### Track A — window-wise metrics on the held-out test set

| experiment | model | precision | recall | F1 | FPR | PR-AUC |
| --- | --- | --- | --- | --- | --- | --- |
| Exp 1-3 (chronological) | robust z-score baseline | 0.75 | 0.67 | 0.71 | 0.13 | 0.82 |
| Exp 1-3 (chronological) | **Isolation Forest** | 0.69 | 1.00 | **0.82** | 0.27 | 0.70 |
| Exp 3 (chronological) | Random Forest (supervised) | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 |
| Exp 4 (held-out fault) | robust z-score baseline | 0.85 | 0.94 | **0.90** | 0.08 | 0.78 |
| Exp 4 (held-out fault) | **Isolation Forest** | 0.75 | 1.00 | **0.86** | 0.17 | 0.74 |
| Exp 4 (held-out fault) | Random Forest (supervised) | 1.00 | 0.50 | **0.67** | 0.00 | 0.70 |

**Reading Exp 1-3:** the Isolation Forest beats the baseline on F1 (0.82 vs
0.71) and catches every anomalous window (recall 1.0); the baseline is more
conservative (higher precision, lower FPR). The supervised Random Forest scores
a perfect 1.0 — but the test set here contains the **same fault types** it
trained on (constant +400 ms latency and 25%-Bernoulli errors are trivially
learnable). Exp 4 is the real test.

**Reading Exp 4 (the important one):** train on latency + error faults only,
test on `publish_failure` + `traffic_surge` — fault types no model saw.
Confusion matrices (test n = 108, 36 anomalous):

| model | TP | FP | FN | TN | what happened |
| --- | --- | --- | --- | --- | --- |
| robust z-score | 34 | 6 | 2 | 66 | generalises — flags unfamiliar faults from "not normal" |
| Isolation Forest | 36 | 12 | 0 | 60 | generalises — catches **all** held-out-fault windows, some extra false alarms |
| Random Forest (supervised) | 18 | 0 | 18 | 72 | **misses an entire held-out fault class** — it learned fault *signatures*, not *normal* |

The supervised model went from F1 1.0 (seen faults) to **0.67 (unseen fault,
recall 0.50)**. The unsupervised detectors held at F1 0.86-0.90. **This is the
argument for shipping the Isolation Forest as the live detector** — a real
platform meets failure modes it has no labels for.

### Track B — NAB (methodology check, macro-averaged per family)

| family | model | precision | recall | F1 | PR-AUC | ROC-AUC |
| --- | --- | --- | --- | --- | --- | --- |
| realKnownCause | robust z-score | 0.18 | 0.20 | 0.18 | 0.20 | 0.66 |
| realKnownCause | Isolation Forest | 0.17 | 0.12 | 0.14 | 0.18 | 0.63 |
| realAWSCloudwatch | robust z-score | 0.14 | 0.43 | 0.14 | 0.12 | 0.44 |
| realAWSCloudwatch | Isolation Forest | 0.11 | 0.33 | 0.12 | 0.13 | 0.49 |

Per-series ROC-AUC (Isolation Forest) tells the real story:

| series | ROC-AUC | verdict |
| --- | --- | --- |
| `machine_temperature_system_failure` | 0.82 | methodology **works** (sustained level shift) |
| `ambient_temperature_system_failure` | 0.64 | partial |
| `ec2_cpu_utilization_5f5533` | 0.62 | partial |
| `ec2_cpu_utilization_fe7f93` | 0.37 | **fails** |
| `nyc_taxi` | 0.44 | **fails** (strong daily/weekly seasonality) |

**Honest conclusion:** the rolling-feature methodology transfers to real-world
series that look like our telemetry (sudden or sustained shifts) and **fails on
strongly seasonal series** it has no seasonal features for. NAB is a hard
benchmark; this is expected and is exactly why [ADR-004](../decisions/adr-004-datasets-vs-live-telemetry.md)
keeps the two tracks separate and the shipped model scoped to the live feature
space. Track B measures the *method*, not the deployed model.

## 14. Limitations

* **Synthetic, stylised faults.** Injected latency is a constant add; injected
  errors are IID Bernoulli. Real degradations are correlated, gradual, and
  messier. All Track A numbers are "on this distribution".
* **Small dataset** — `run_a` is 144 windows (~4 h of scripted traffic),
  `run_b` 108. The chronological test set is 48 windows. Enough for IF /
  baseline; not for deep models, and confidence intervals on the metrics are
  wide (a couple of windows swing F1 by ~0.05). More data needs more collection
  time, not more code.
* **Validation set is thin and surge-only.** With three identical plan cycles,
  the 17% validation slice happens to contain only `traffic_surge` anomalies,
  so the F1-calibrated threshold is tuned against one fault type. It still
  generalises (Exp 4), but a longer plan with staggered cycles would give a
  better-mixed validation set.
* **Held-out ≠ wild.** Experiment 4 tests an *unseen fault type from the same
  generator*, not a genuinely novel real-world incident.
* **Single service, single environment.** No cross-service or seasonal signal.
* **Supervised RF scores 1.0 on seen faults** because the injected faults are
  perfectly separable in feature space — that number is a ceiling artefact, not
  a claim.
* **Publish-latency percentiles unavailable** at the current Phase 1 histogram
  bucket configuration — mean only.
* **`traffic_surge` labelling** is a defensible judgement call, not ground
  truth in a strict sense.
* NAB evaluation uses standard windowed P/R/F1, not NAB's official scoring
  profile (documented simplification).

## 15. Reproducibility

```bash
pip install -e ".[dev,ml]"
make ml-test                       # ML unit tests
make ml-experiments                # all 6 experiments -> artifacts/reports/ + summary.md
make ml-experiment NAME=exp2_isolation_forest_sentinelops
# regenerate Track A data (needs Docker + a host orders-service, ~30 min/run):
docker compose up -d kafka ; make run-orders &
python -m ml.collection.collector --run-id run_a --plan main --seed 42
python -m ml.collection.collector --run-id run_b --plan holdout --seed 7
make data-prepare
make nab-download                  # Track B data (network; not committed)
```

Everything is seeded from `ml.config.RANDOM_SEED = 42`. `tests/ml/test_pipeline.py`
asserts an experiment produces byte-identical metrics on a re-run.

## 16. How Phase 2 connects to Phase 3

`ml/inference/DetectorService` is the boundary. Phase 3 will, per telemetry
window:

```python
from ml.inference import DetectorService

svc = DetectorService.load(
    "artifacts/models/exp2_isolation_forest_sentinelops__isolation_forest.joblib"
)
result = svc.score_window(
    signal_record
)  # AnomalyResult(score, is_anomaly, threshold, features, ...)
```

and turn `result.is_anomaly` windows into anomaly signals to **correlate** into
incidents. Phase 2 builds none of that correlation logic — only this importable,
model-agnostic entry point, with the streaming featurizer proven equivalent to
the batch training features.
