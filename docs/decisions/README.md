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
| [024](adr-024-remediation-domain-and-action-catalogue.md) | Phase 5 remediation domain — closed action catalogue, structural no-command guarantee | Accepted |
| [025](adr-025-deterministic-remediation-policy-engine.md) | Deterministic remediation policy engine (Phase 5B) — no LLM, fail closed, injectable history port | Accepted |
| [026](adr-026-remediation-persistence-and-approval-workflow.md) | Remediation persistence + human approval workflow (Phase 5C) — own Alembic lineage, immutable approvals, concurrency-safe, demo identity | Accepted |
| [027](adr-027-allow-listed-executor-and-local-simulation.md) | Allow-listed executor abstraction + LocalSimulationExecutor (Phase 5D) — typed proposal in, no infrastructure, dry-run, single execution | Accepted |
| [028](adr-028-append-only-remediation-audit-trail.md) | Append-only remediation audit trail (Phase 5E) — immutable events, four-layer append-only enforcement, per-transition atomicity, secret redaction, read-only API | Accepted |
| [029](adr-029-recovery-verification.md) | Recovery verification (Phase 5F) — deterministic observe-only verifier, verifier-owned thresholds, bounded virtual-clock poll loop, execution-style transactions, idempotent, no LLM / no execution authority | Accepted |
| [030](adr-030-remediation-lifecycle-events.md) | Remediation lifecycle events on Kafka (Phase 5G) — publisher-only, closed versioned `RemediationLifecycleV1` contract on `remediation.events` keyed by `remediation_id`, best-effort after-commit publication (no outbox), deterministic `event_id`, Kafka never an execution channel | Accepted |
| [031](adr-031-mlflow-tracking-and-registry.md) | MLflow for experiment tracking and the model registry (Phase 6A) — local Compose deployment, `mlflow-skinny` client (pandas/numpy untouched), Postgres backend store + HTTP-served artifacts, additive fail-safe tracking, aliases not stages | Accepted |
| [032](adr-032-model-alias-strategy.md) | Model promotion uses MLflow aliases, not stages (Phase 6B) — `candidate` / `champion` / `previous-champion`, closed code-defined set, `models:/<name>@champion` resolution; deprecated `Staging`/`Production` stages never used | Accepted |
| [033](adr-033-model-promotion-criteria.md) | Deterministic model-promotion criteria (Phase 6B) — `PromotionPolicy` floors (F1 0.75 / recall 0.90 / PR-AUC 0.60) + F1 regression tolerance 0.05, grounded in committed Phase 2 numbers; pure `evaluate_candidate`, no LLM; champion preserved on rejection | Accepted |
| [034](adr-034-drift-detection-methodology.md) | Data-drift detection via PSI (Phase 6D) — per-feature Population Stability Index against a quantile-binned training baseline frozen with the champion; standard `<0.1 / 0.1-0.25 / >=0.25` bands; label-free, deterministic, no LLM; prediction drift reported separately from feature drift and from (label-dependent) performance degradation | Accepted |

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
