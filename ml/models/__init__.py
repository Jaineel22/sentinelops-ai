"""Anomaly detectors: a statistical baseline, the primary Isolation Forest, and
a supervised comparator. All share the :class:`~ml.models.base.AnomalyDetector`
interface so experiments and inference treat them uniformly."""

from ml.models.base import AnomalyDetector, DetectorMetadata
from ml.models.baseline import RobustZScoreDetector
from ml.models.isolation_forest import IsolationForestDetector
from ml.models.supervised import RandomForestDetector

__all__ = [
    "AnomalyDetector",
    "DetectorMetadata",
    "IsolationForestDetector",
    "RandomForestDetector",
    "RobustZScoreDetector",
]
