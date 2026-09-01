"""rca-agent — Phase 4: AI root-cause investigation.

Consumes ``incident.opened`` events from Phase 3, investigates the incident with
a **fixed, allow-listed set of read-only evidence tools**, and produces a
structured, evidence-grounded RCA report. The investigation runs as a bounded
LangGraph state machine (Sub-phase 4C); the LLM only interprets evidence and
proposes hypotheses — it never controls the deterministic safety boundaries
(tool allow-list, limits, validation, persistence).

Phase 4 explains incidents. It does not detect anomalies (Phase 2), correlate
incidents (Phase 3), or execute remediation (Phase 5). Its remediation output is
a structured *recommendation* that always requires human approval.
"""

__version__ = "0.4.0"
SERVICE_NAME = "rca-agent"
