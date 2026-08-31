# ADR-001: Event-driven architecture with Kafka as the backbone

- Status: Accepted
- Date: 2026-08-31

## Context

SentinelOps ingests continuous telemetry and moves each incident through a
pipeline: detection → correlation → investigation → approval → remediation →
verification. These stages evolve at different rates, have different scaling
needs, and must remain auditable. A tightly coupled request/response design
would force lock-step deployment and make it hard to add consumers (e.g. audit,
analytics, retraining data capture) later.

## Decision

The system is **event-driven**. **Apache Kafka** is the event backbone. Each
pipeline stage is a service that consumes events from one or more topics and
produces events to others. Topics and event schemas are explicit and versioned.
Kafka's retention also gives us a replayable record of what happened, which
supports auditability and building training datasets.

## Alternatives considered

- **Synchronous REST/gRPC between services.** Simpler to start, but couples
  deployment and availability, and makes fan-out (adding consumers) invasive.
- **A managed queue (SQS) or lighter broker (RabbitMQ, NATS).** Fine for
  work-queue semantics, but weaker on durable, replayable, multi-consumer logs.
  Kafka is also the industry-standard skill to demonstrate for this domain.
- **A database outbox / polling.** Works at small scale but reinvents a log
  poorly and does not decouple consumers.

## Consequences

- Services are independently deployable and testable; new consumers are additive.
- We take on operational complexity: running Kafka (locally via Docker Compose,
  later on Kubernetes), schema management, and consumer-lag monitoring.
- Every service must handle at-least-once delivery (idempotency, dedup).
- Kafka is **not** introduced in Phase 0; it arrives in Phase 1 with the first
  producing service.
