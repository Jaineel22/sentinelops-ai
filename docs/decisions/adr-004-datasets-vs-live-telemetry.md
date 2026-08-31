# ADR-004: Evaluate public datasets separately from live telemetry

- Status: Accepted
- Date: 2026-08-31

## Context

Public benchmarks — HDFS and BGL (log anomaly detection) and NAB (streaming
metric anomalies) — are valuable for methodology, baselines, and comparability.
But their feature spaces do not match SentinelOps' live telemetry. A model
trained on HDFS log templates cannot meaningfully score live service latency
metrics. Blurring the two would produce impressive-looking but invalid claims.

## Decision

Maintain two clearly separated tracks:

1. **Offline benchmark track.** Use HDFS / BGL / NAB for experimentation,
   algorithm selection, and reporting standard metrics. Results are labelled as
   benchmark results on that dataset.
2. **Live detection track.** The production anomaly-detection pipeline is
   trained and evaluated only on telemetry whose feature space matches the live
   SentinelOps system (real + production-like synthetic telemetry with injected
   faults).

We never train on one and claim detection capability on the other. Each track
has its own data directory, feature definitions, and evaluation reports.

## Alternatives considered

- **Use benchmark-trained models directly on live metrics.** Invalid; feature
  mismatch. Rejected.
- **Only synthetic/live data, no public benchmarks.** Loses comparability and a
  credible baseline story. Rejected.
- **Merge datasets into one feature space.** Not semantically meaningful across
  these sources; would require heavy, lossy transformation. Rejected.

## Consequences

- Two evaluation pipelines and two sets of reported numbers, each with explicit
  scope.
- Slightly more work, but the ML claims are defensible in a technical interview.
- Relevant from Phase 2; no datasets are committed in Phase 0.
