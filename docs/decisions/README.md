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
