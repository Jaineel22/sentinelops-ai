# Architecture Decision Records

Each ADR captures one significant, hard-to-reverse decision: its **context**,
the **decision**, **alternatives considered**, and **consequences**.

ADRs are immutable once accepted. To change a decision, add a new ADR that
supersedes the old one and update the status line.

| ADR | Title | Status |
| --- | --- | --- |
| [001](adr-001-event-driven-architecture.md) | Event-driven architecture with Kafka as the backbone | Accepted |
| [002](adr-002-ml-and-llm-separation.md) | Separate ML anomaly detection from LLM-based RCA | Accepted |
| [003](adr-003-human-in-the-loop-remediation.md) | Human approval required for remediation | Accepted |
| [004](adr-004-datasets-vs-live-telemetry.md) | Evaluate public datasets separately from live telemetry | Accepted |
| [005](adr-005-incremental-delivery.md) | Build incrementally in phases, not all at once | Accepted |
| [006](adr-006-kafka-local-deployment-and-client.md) | Local Kafka via single-node KRaft; aiokafka as the client | Accepted |
| [007](adr-007-opentelemetry-instrumentation-standard.md) | OpenTelemetry is the telemetry instrumentation standard | Accepted |
| [008](adr-008-events-vs-telemetry.md) | Business events (Kafka) are separate from observability telemetry (OTel) | Accepted |
| [009](adr-009-controlled-failure-injection.md) | Controlled, development-only failure injection | Accepted |
| [010](adr-010-phase1-synchronous-publish.md) | Phase 1 order publishing is synchronous and fail-closed | Accepted |

## Template

```markdown
# ADR-NNN: Title

- Status: Proposed | Accepted | Superseded by ADR-XXX
- Date: YYYY-MM-DD

## Context
## Decision
## Alternatives considered
## Consequences
```
