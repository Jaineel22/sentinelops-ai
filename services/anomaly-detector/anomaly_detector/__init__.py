"""anomaly-detector — the streaming wrapper that completes the Phase 2 -> Phase 3
handoff.

It polls ``orders-service``'s ``/metrics`` endpoint on a fixed interval, turns
each pair of scrapes into a telemetry window (reusing ``ml.data.prepare``),
scores it with the Phase 2 ``ml.inference.DetectorService``, and — when a window
is anomalous — publishes an ``anomaly.detected`` event onto Kafka for the
incident-correlator to consume.

Deliberately thin: it owns no ML logic of its own.
"""

__version__ = "0.3.0"
SERVICE_NAME = "anomaly-detector"
