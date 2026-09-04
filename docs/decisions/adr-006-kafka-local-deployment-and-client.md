# ADR-006: Local Kafka via single-node KRaft; aiokafka as the client

- Status: Accepted
- Date: 2026-08-31

## Context

[ADR-001](adr-001-event-driven-architecture.md) fixed Kafka as the backbone.
Phase 1 needs a concrete local deployment and a Python client. Constraints:
runs on a laptop via Docker Compose, easy to reason about, replaceable by a
multi-broker cluster later, and no dependency on abandoned tutorial patterns.

## Decision

**Broker:** the official `apache/kafka` image in **KRaft mode** (no ZooKeeper),
a single node acting as both controller and broker, replication factor 1. Three
listeners: `PLAINTEXT` for in-cluster traffic (`kafka:9092`), `CONTROLLER` for
the raft quorum, and `PLAINTEXT_HOST` (`localhost:29092`) so host processes
(traffic generator, integration tests) can reach it. Auto topic creation is
**off**; `orders-service` creates `orders.events` on startup for dev
convenience.

**Client:** `aiokafka`. The services are async (FastAPI/asyncio); aiokafka is a
native-async producer/consumer, so no thread-pool bridging. It is pure-Python
with prebuilt wheels and supports message headers (needed for trace-context
propagation — [ADR-008](adr-008-events-vs-telemetry.md)).

## Alternatives considered

- **ZooKeeper-based Kafka** (Confluent tutorial default): an extra service and a
  deprecated architecture. Rejected.
- **Bitnami Kafka image**: recent licensing/hardening changes make it a moving
  target for a learning project. Rejected in favour of the Apache image.
- **`confluent-kafka` (librdkafka)**: fastest and most "production", but a
  blocking API that needs `run_in_executor` wrapping throughout. Revisit if
  Phase 9 load testing shows aiokafka is the bottleneck.
- **`kafka-python`**: pure-Python but slower-moving; aiokafka is the async
  evolution of the same lineage.
- **Redpanda**: single binary, Kafka-compatible, pleasant locally. Deferred —
  staying on genuine Apache Kafka keeps the later multi-broker/K8s story
  standard.

## Consequences

- One command (`docker compose up`) gives a working broker.
- RF=1 / single node means **no durability or HA** — fine for local dev, not a
  model for production. The compose file is structured so the broker block can
  be swapped for a cluster without touching application code.
- Switching to `confluent-kafka` later is contained to `kafka_producer.py` and
  `consumer.py` because the app depends on the `EventPublisher` protocol.
