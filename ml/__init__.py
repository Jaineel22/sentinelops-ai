"""SentinelOps AI — ML anomaly-detection subsystem (Phase 2).

Offline pipeline: generate service telemetry from the Phase 1 ``orders-service``,
turn it into time-windowed features, train a statistical baseline and an
Isolation Forest, and evaluate both with leakage-safe chronological splits.

Two data tracks are kept strictly separate (ADR-004):

* Track A — SentinelOps operational telemetry (``ml.collection`` / ``ml.data``).
* Track B — the public NAB benchmark (``ml.data.nab``).

``ml.inference.DetectorService`` is the clean boundary Phase 3 will call.
"""

__version__ = "0.2.0"
