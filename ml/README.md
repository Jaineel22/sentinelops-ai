# ml/ — anomaly-detection subsystem (Phase 2)

Offline pipeline that learns "normal" SentinelOps behaviour and flags anomalous
telemetry windows. Two **separate** data tracks (ADR-004):

* **Track A** — SentinelOps operational telemetry from the Phase 1
  `orders-service` (`ml/collection`, `ml/data/prepare.py`).
* **Track B** — the public NAB benchmark (`ml/data/nab.py`), a methodology check
  only.

## Layout

| Path | Responsibility |
| --- | --- |
| `config.py` | seeds + repo-relative paths |
| `collection/` | drive scenarios + scrape `/metrics` → raw snapshots |
| `data/` | `prometheus_parse`, `validation`, `prepare` (raw→windows), `nab`, `schema` |
| `features/engineering.py` | `build_features()` — **shared by training and inference** |
| `splits.py` | `chronological_split`, `held_out_fault_split` (leak-safe) |
| `models/` | `RobustZScoreDetector` (baseline), `IsolationForestDetector` (primary), `RandomForestDetector` (supervised comparator) — one interface |
| `evaluation/` | point-wise + event-wise metrics, plots |
| `experiments/` | `catalog.py` (the 6 experiments), `runner.py`, `__main__` CLI |
| `inference/` | `DetectorService` — the clean Phase 3 boundary |

## Reproduce

```bash
pip install -e ".[dev,ml]"

# 1. (optional) regenerate Track A — needs Docker + host orders-service, ~20 min
docker compose up -d kafka
make run-orders &                       # host orders-service on :8001
python -m ml.collection.collector --run-id run_a --plan main
python -m ml.collection.collector --run-id run_b --plan holdout
make data-prepare                        # -> ml/data/processed/sentinelops/

# 2. Track B benchmark data (downloaded, not committed)
make nab-download

# 3. run every experiment on the committed data
make ml-experiments                      # -> artifacts/reports/  + summary.md

# 4. tests
make ml-test
```

The committed `ml/data/processed/sentinelops/{run_a,run_b}/` datasets mean steps
3-4 work without step 1. Full write-up:
[docs/architecture/phase-2.md](../docs/architecture/phase-2.md).
