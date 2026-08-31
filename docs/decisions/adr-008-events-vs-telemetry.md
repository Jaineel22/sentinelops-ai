# ADR-008: Business events (Kafka) are separate from observability telemetry (OpenTelemetry)

- Status: Accepted
- Date: 2026-08-31

## Context

Both Kafka and OpenTelemetry move "stuff that happened" around the system. It is
tempting to collapse them — e.g. publish metrics/traces onto a Kafka topic
because the broker is already there, or treat `order.created` as just another
telemetry signal. Getting this boundary wrong now would badly confuse the ML
pipeline in Phase 2, which must know exactly what its input feature space is.

## Decision

Keep them as two separate planes with different purposes:

| | Business events | Observability telemetry |
| --- | --- | --- |
| Transport | Apache Kafka | OpenTelemetry (OTLP / Prometheus scrape) |
| Example | `order.created` | request latency histogram, a trace span, a log line |
| Contract | Versioned event envelope (`docs/architecture/events.md`) | OTel semantic conventions |
| Consumers | Other services acting on domain facts (correlation, ML feature build) | Monitoring backends, dashboards, the RCA agent's evidence tools |
| Delivery | At-least-once, consumers must be idempotent | Best-effort, lossy under pressure is acceptable |

Telemetry does **not** flow through Kafka in Phase 1. The only crossover is
**correlation**: a business event carries the originating `trace_id` (in the
envelope and as a `traceparent` message header) so a consumer can line an event
up with the trace that produced it.

## Alternatives considered

- **Telemetry over Kafka** (Kafka as the OTLP transport). Adds broker load and
  coupling for no Phase-1 benefit; OTLP/scrape is the standard path. An OTel
  Collector can add a Kafka exporter later if a real need appears.
- **One unified "event" stream.** Destroys the delivery-semantics and
  schema-contract distinction the ML and correlation phases rely on.

## Consequences

- Two systems to run and understand, but each stays simple and single-purpose.
- The ML pipeline's input is unambiguous: it builds features from telemetry, not
  from the business event stream (Phase 2 will state which).
- Correlation is explicit and testable (`traceparent` header + `trace_id`
  field), not magic.
