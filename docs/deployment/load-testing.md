# Load testing

The k6 scenarios live in `tests/load`:

```bash
BASE_URL=http://localhost:8000 k6 run tests/load/api.js
ORDERS_URL=http://localhost:8001 k6 run tests/load/incident-detection.js
RCA_URL=http://localhost:8004 k6 run tests/load/rca.js
```

They enforce error-rate and latency thresholds, but require a running
environment. Results are not generated in CI by default.
