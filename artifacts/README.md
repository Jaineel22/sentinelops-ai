# artifacts/

Outputs of the Phase 2 ML pipeline.

| Path | Committed? | What |
| --- | --- | --- |
| `reports/<experiment>/metrics.json` | **yes** | Full metric set + config + seed + git SHA for one experiment. |
| `reports/<experiment>/*.png` | **yes** | Score timelines, metric-comparison bars, per-series PR-AUC. |
| `reports/summary.md` / `summary.json` | **yes** | Cross-experiment table (written by `python -m ml.experiments run all`). |
| `models/*.joblib` | no (git-ignored) | Trained detector bundles. Regenerate with `make ml-experiments`. |

Regenerate everything:

```bash
make ml-experiments        # -> reports/ (+ models/)
```

Committed reports are the numbers quoted in
[docs/architecture/phase-2.md](../docs/architecture/phase-2.md). They are
reproducible: the pipeline is seeded from `ml.config.RANDOM_SEED`.
