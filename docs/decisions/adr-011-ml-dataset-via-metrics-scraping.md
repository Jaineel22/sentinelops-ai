# ADR-011: Build the Track A ML dataset by scraping `/metrics`

- Status: Accepted
- Date: 2026-08-31

## Context

Phase 2 needs a labelled time-series dataset of SentinelOps operational
behaviour. The Phase 1 `orders-service` already exposes exactly the signals we
want (request/error/latency/publish metrics) at `GET /metrics` in Prometheus
format, and `scripts/generate_traffic.py` can drive labelled scenarios. There is
no Prometheus server yet (Phase 7). We must not change Phase 1 behaviour.

## Decision

A collector (`ml/collection/collector.py`) drives a **deterministic, interleaved**
sequence of scenarios against a running `orders-service` and **scrapes
`/metrics` every 10 s**, recording cumulative counter/histogram values plus the
scenario active at each scrape. Fault segments are evenly spaced between normal
segments (the plan is repeated identical cycles), so a later chronological
split gives train / validation / test a proportional share of each fault type.
A prepare step (`ml/data/prepare.py`) turns consecutive scrapes into per-window
rate signals (counter deltas ÷ elapsed time; histogram-bucket deltas → latency
percentiles).

* This is exactly how Phase 7's Prometheus will observe the service — the
  feature-extraction code is reusable later.
* **Labels are orchestrator ground truth**, stored in a *separate* `labels.csv`:
  each window's label is the scenario active for that interval. Windows that
  span a scenario boundary or a process restart (counter reset) are dropped.
* The failure-injection counters (`orders_failure_injection_total`) and the
  scenario label are recorded for debugging but **never** used as model
  features — see the `FORBIDDEN_FEATURE_SUBSTRINGS` allow-list guard.
* Two canonical runs are **committed** as small CSVs so training/evaluation/CI
  are reproducible without a ~20-minute live collection. Raw scrapes are
  git-ignored.

## Alternatives considered

- **Parse the structured JSON logs instead.** Richer (per-request) but couples
  the dataset to log format and needs its own aggregation; the metrics endpoint
  is the intended monitoring interface.
- **A pure statistical telemetry simulator** (no real service). Removes the real
  system from the loop and weakens every "credible dataset" claim. Rejected.
- **Add histogram-bucket config to Phase 1** so publish-latency percentiles are
  available. Deferred: publish-latency *mean* is enough for Phase 2 and the
  change belongs with the Phase 7 observability work.

## Consequences

- Regenerating Track A needs Docker + `orders-service` + ~20 min. Documented;
  the committed dataset means most work doesn't need it.
- The dataset is only as realistic as the fault injector (stylised faults). All
  reported numbers are scoped "on this synthetic distribution".
- Boundary/reset window drops cost a little data for a lot of label clarity.
