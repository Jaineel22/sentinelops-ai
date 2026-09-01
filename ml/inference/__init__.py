"""Inference boundary for Phase 3.

Phase 3 (incident correlation) will, per telemetry window:

    signal record -> DetectorService.score_window() -> AnomalyResult
                     (features)         (anomaly score, is_anomaly)

and turn ``is_anomaly`` windows into anomaly signals to correlate. Phase 2 does
not build any of that — it only provides this clean, importable entry point.
"""

from ml.inference.detector_service import AnomalyResult, DetectorService
from ml.inference.featurizer import StreamFeaturizer

__all__ = ["AnomalyResult", "DetectorService", "StreamFeaturizer"]
