"""incident-correlator — Phase 3.

Consumes ``anomaly.detected`` events, groups related anomalies for the same
service into a single **incident** with deterministic, explainable rules,
persists incidents + evidence + lifecycle history in PostgreSQL, and exposes an
Incident API that Phase 4 (RCA / AI investigation) will consume.

No LLM, no RCA, no remediation — those are later phases.
"""

__version__ = "0.3.0"
SERVICE_NAME = "incident-correlator"
