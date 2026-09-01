# ADR-013: NAB as the independent benchmark track

- Status: Accepted
- Date: 2026-08-31

## Context

[ADR-004](adr-004-datasets-vs-live-telemetry.md) requires a public benchmark
track kept **separate** from SentinelOps telemetry, to check that the
methodology holds outside our own fault generator. We must pick a dataset that
is genuinely public, downloadable without a request form, and legally usable.

## Decision

Use the **Numenta Anomaly Benchmark (NAB)** — `github.com/numenta/NAB`, the
`realKnownCause` and `realAWSCloudwatch` families (machine temperature, NYC
taxi, ambient temperature, EC2 CPU utilisation), with the `combined_windows.json`
labels.

* **Not committed to this repo.** NAB's `data/` directory is under **AGPL-3.0**;
  this repo is MIT. The data is downloaded on demand by `ml/data/nab.py` and
  **content-pinned by sha256** in `ml/data/nab_manifest.json` (committed). Re-download verifies the hashes.
* Track B runs the **same methodology** — robust z-score baseline, Isolation
  Forest on engineered *rolling* features, chronological split, window-wise
  evaluation — on these univariate series. Experiments 5 and 6.
* Track B is **never** used to train a model that scores SentinelOps telemetry.
  The feature spaces are unrelated; this is a methodology check, not transfer
  learning.

## Alternatives considered

- **Yahoo Webscope S5.** Good dataset, but access requires an application form —
  fails "publicly accessible / reproducible download". Rejected.
- **HDFS / BGL logs.** Log-anomaly detection — a different problem (sequence /
  template mining) from metric-anomaly detection. Out of scope for Phase 2.
- **Commit a NAB subset.** Avoids the download step but creates an AGPL / MIT
  licence conflict in the repo. Rejected in favour of the pinned downloader.
- **Synthetic-only (skip Track B).** Loses the independent check and the
  comparability an interviewer will ask about. Rejected.

## Consequences

- Reproducing Track B needs one network download (`make nab-download`); CI can
  cache it or skip Track B when offline.
- Two sets of numbers with clearly different scope. Track B results describe the
  *methodology*, not the shipped model.
