# Local and live RCA demos

## Faculty demo: deterministic local stack

This is the recommended keyless demo path. It uses Docker Compose with
`RCA_MODE=mock`, so it is deterministic and does not call an external LLM.

```bash
export JWT_SECRET_KEY="$(openssl rand -hex 32)"
docker compose up --build -d \
  api kafka postgres orders-service orders-consumer \
  incident-migrate rca-migrate remediation-migrate \
  incident-correlator anomaly-detector rca-agent \
  remediation-controller frontend
docker compose ps
```

Open <http://localhost:3100> and sign in with `admin` / `admin123`.
For the complete backend workflow, run:

```bash
PYTHONPATH=apps/api:services/incident-correlator:services/anomaly-detector:services/rca-agent:services/remediation-controller:apps/orders-service \
python scripts/phase9_verify.py

PYTHONPATH=apps/api:services/incident-correlator:services/anomaly-detector:services/rca-agent:services/remediation-controller:apps/orders-service \
python scripts/remediation_e2e_scenario.py
```

These scenarios verify the RCA report, evidence grounding, JWT-protected API
access, human approval gate, local simulation executor, immutable audit trail,
recovery verification, rejection path, and recovery-failure path.

The optional MLflow, Prometheus, and Grafana services are not needed for the
faculty demo. MLflow uses host port `5000`, which can conflict with macOS
Control Center.

## Live Anthropic RCA demo

### What it shows

This demo exercises the production-shaped path: controlled latency is injected
into the orders service, the anomaly detector and incident correlator create an
incident, and the RCA agent gathers bounded read-only evidence before asking
Anthropic for a structured root-cause report. No remediation is executed.

### Setup

Install Docker, Docker Compose, Python 3, and `curl`. Export a provider key and
a strong shared JWT secret:

```bash
export ANTHROPIC_API_KEY=your-key
export JWT_SECRET_KEY="$(openssl rand -hex 32)"
```

Run it with:

```bash
bash scripts/live_rca_demo.sh
```

Use `KEEP_DEMO_STACK=1` to leave containers running for inspection. To capture
an output artifact:

```bash
bash scripts/record_demo.sh artifacts/live-rca-demo.txt
```

### Sample output

The final JSON contains the incident id, evidence references, confidence, a
root-cause statement, and a recommended action marked as requiring human
approval. Exact wording varies because live model output is not deterministic:

```text
Live RCA report for incident 8e2...
{
  "investigation": {"status": "COMPLETED", ...},
  "report": {
    "root_cause": {"statement": "...latency...", "confidence": 0.87},
    "recommended_action": {"requires_human_approval": true, ...}
  }
}
```

### Mock versus live mode

`RCA_MODE=mock` is the default and uses the deterministic reasoner; it needs no
network access or API key and is the CI mode. `RCA_MODE=live` selects the same
bounded investigation graph with `LLM_PROVIDER=anthropic` and
`LLM_API_KEY=ANTHROPIC_API_KEY`. The model can propose a report, but deterministic
schema validation and the remediation controller's human approval gate remain
authoritative.
