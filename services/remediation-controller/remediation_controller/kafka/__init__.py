"""Remediation lifecycle Kafka integration (Phase 5G).

Publisher-only: the remediation-controller emits versioned
``remediation.events`` lifecycle events after each committed state transition
and consumes **nothing**. The PostgreSQL state machine + append-only audit trail
remain authoritative (ADR-030).
"""

from __future__ import annotations

from remediation_controller.kafka.events import (
    DEFAULT_REMEDIATION_TOPIC,
    is_publishable,
    lifecycle_envelope,
)
from remediation_controller.kafka.publisher import RemediationEventPublisher

__all__ = [
    "DEFAULT_REMEDIATION_TOPIC",
    "RemediationEventPublisher",
    "is_publishable",
    "lifecycle_envelope",
]
