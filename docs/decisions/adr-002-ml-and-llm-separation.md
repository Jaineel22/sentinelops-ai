# ADR-002: Separate ML anomaly detection from LLM-based RCA

- Status: Accepted
- Date: 2026-08-31

## Context

SentinelOps has two very different reasoning problems:

1. **Detecting** abnormal behaviour in high-volume numeric/categorical telemetry,
   continuously and cheaply, with measurable statistical quality.
2. **Investigating** an incident: gathering heterogeneous evidence, reasoning
   about causal chains, and explaining a root cause in natural language.

Collapsing both into "call an LLM" would be slow, expensive, non-deterministic,
hard to evaluate, and would misuse the LLM for a task where trained statistical
models are stronger.

## Decision

Keep the two responsibilities in separate components:

- **Machine learning** (scikit-learn / XGBoost, later PyTorch only if justified)
  owns anomaly detection. It is trained, versioned, and evaluated with real
  metrics (precision, recall, F1, PR-AUC, false-positive rate, detection
  latency).
- **The AI agent** (LangGraph or equivalent, backed by an LLM API with tool
  calling) owns incident investigation, evidence correlation, root-cause
  analysis, and remediation proposals.

An LLM API call is explicitly **not** counted as the project's ML component.

## Alternatives considered

- **LLM-only** (prompt the model with raw telemetry). Poor cost/latency, no
  rigorous evaluation story, weak on numeric pattern detection.
- **ML-only** (hand-coded heuristics for RCA). Brittle, and cannot produce
  human-readable, evidence-linked explanations across diverse incidents.
- **One model for both.** No single model class does both well; separation also
  lets each evolve and be evaluated independently.

## Consequences

- Two evaluation regimes: statistical metrics for detection, and agent-quality
  evaluation (evidence grounding, RCA correctness on known incidents) for the
  agent.
- Clear interface: ML emits anomaly events; the agent consumes incidents.
- More components to build and operate, introduced in different phases (ML in
  Phase 2, agent in Phase 4).
