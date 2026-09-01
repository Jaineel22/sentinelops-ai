"""``sentinelops_common`` — shared building blocks for SentinelOps services.

Small, dependency-light helpers that more than one service needs and that must
not drift between them:

* :mod:`sentinelops_common.events`  — the canonical Kafka event envelope.
* :mod:`sentinelops_common.obs`     — structured JSON logging + OpenTelemetry.
* :mod:`sentinelops_common.kafka`   — a JSON producer and an idempotent
  manual-commit consumer loop with dead-letter handling.

This is a library, never a running process. Phase 1's ``orders-service``
predates it and keeps its own equivalent logging/telemetry modules; new services
(Phase 3+) use these.
"""

__version__ = "0.3.0"
