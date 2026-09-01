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
| [011](adr-011-ml-dataset-via-metrics-scraping.md) | Build the Track A ML dataset by scraping `/metrics` | Accepted |
| [012](adr-012-isolation-forest-primary-detector.md) | Isolation Forest is the primary anomaly detector | Accepted |
| [013](adr-013-nab-benchmark-track.md) | NAB as the independent benchmark track | Accepted |
| [014](adr-014-postgresql-for-incident-state.md) | PostgreSQL for incident state | Accepted |
| [015](adr-015-deterministic-anomaly-correlation.md) | Deterministic anomaly-to-incident correlation | Accepted |
| [016](adr-016-idempotent-kafka-consumer.md) | Idempotent Kafka consumer with at-least-once semantics | Accepted |
| [017](adr-017-incident-state-machine.md) | Incident lifecycle state machine | Accepted |
| [018](adr-018-kafka-partitioning-strategy.md) | Kafka partitioning by correlation key | Accepted |
| [019](adr-019-rca-agent-service-and-boundary.md) | Phase 4 RCA agent — separate service, shared database, structural Phase 5 boundary | Accepted |
| [020](adr-020-controlled-read-only-evidence-tools.md) | Controlled, read-only, allow-listed evidence tools | Accepted |
| [021](adr-021-llm-boundary-and-injection-defense.md) | LLM boundary — deterministic authority, structured output, prompt-injection quarantine | Accepted |
| [022](adr-022-live-llm-provider.md) | Live LLM provider — Anthropic behind the existing boundary, forced-tool-use structured output | Accepted |
| [023](adr-023-rca-agent-integration.md) | RCA-agent integration — event idempotency, async API, no Phase 3 write-back | Accepted |

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
