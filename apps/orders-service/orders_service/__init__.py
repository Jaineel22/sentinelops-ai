"""orders-service — a small, production-like demo application.

It is **not** a SentinelOps internal component. It exists to generate realistic
operational activity: HTTP traffic, business events published to Kafka, and
OpenTelemetry traces/metrics/structured logs that later phases will observe.

Phase 1 scope only. No ML, no incident logic, no remediation.
"""

__version__ = "0.1.0"
SERVICE_NAME = "orders-service"
