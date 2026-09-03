"""Phase 5F — recovery verification.

*"Execution succeeded"* is not *"the system recovered"*. After a remediation
reaches ``EXECUTED`` this package runs a separate, deterministic, evidence-based
check that transitions the remediation to ``RECOVERED`` or ``RECOVERY_FAILED``:

    EXECUTED ─► VERIFYING ─► RECOVERED | RECOVERY_FAILED

* :class:`RecoveryVerifier` — a bounded poll loop over a
  :class:`HealthProbe`, evaluating the verifier's own thresholds. No LLM.
* :class:`SimulatedHealthProbe` — reads the executor's in-process
  ``SimulationState``; no real infrastructure, no I/O.
* :class:`VerificationResult` — the persisted / API shape (one row per
  remediation, ``UNIQUE(remediation_id)``).

The verifier only **observes**. It has no execution authority: it cannot run a
command, call infrastructure, re-execute the remediation, or bypass approval.
"""

from __future__ import annotations

from remediation_controller.recovery.config import (
    DEFAULT_RECOVERY_CONFIG,
    RecoveryVerificationConfig,
)
from remediation_controller.recovery.health import HealthProbe, SimulatedHealthProbe
from remediation_controller.recovery.model import (
    VERIFICATION_ID_RE,
    VERIFIER_TYPE_LOCAL,
    VERIFIER_VERSION,
    HealthSnapshot,
    HealthStatus,
    RecoveryCheck,
    RecoveryOutcome,
    VerificationResult,
    VerificationStatus,
    new_verification_id,
)
from remediation_controller.recovery.verifier import (
    DeterministicRecoveryVerifier,
    RecoveryVerifier,
    build_default_verifier,
)

__all__ = [
    "DEFAULT_RECOVERY_CONFIG",
    "VERIFICATION_ID_RE",
    "VERIFIER_TYPE_LOCAL",
    "VERIFIER_VERSION",
    "DeterministicRecoveryVerifier",
    "HealthProbe",
    "HealthSnapshot",
    "HealthStatus",
    "RecoveryCheck",
    "RecoveryOutcome",
    "RecoveryVerificationConfig",
    "RecoveryVerifier",
    "SimulatedHealthProbe",
    "VerificationResult",
    "VerificationStatus",
    "build_default_verifier",
    "new_verification_id",
]
