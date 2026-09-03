"""Recovery-verification configuration (Phase 5F).

The **safety thresholds** (max error rate, latency, readiness requirement) are
*code-defined defaults*, immutable at runtime — the same discipline as the policy
engine config (ADR-025): a verdict of "recovered" must not be weakenable by an
environment variable. Only the two operational timing knobs (verification window
and poll interval) are exposed to ``AppSettings`` for tuning; they cannot make a
degraded service pass, only change how long / how often the verifier looks.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RecoveryVerificationConfig(BaseModel):
    """Bounded, immutable knobs for one verification run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --- operational (tunable via AppSettings) ---
    timeout_seconds: int = Field(default=30, ge=1, le=3600)
    poll_interval_seconds: float = Field(default=3.0, gt=0.0, le=600.0)

    # --- safety thresholds (code-defined; not env-tunable) ---
    max_error_rate: float = Field(default=0.05, ge=0.0, le=1.0)
    min_success_rate: float = Field(default=0.95, ge=0.0, le=1.0)
    max_latency_p95_ms: float = Field(default=750.0, gt=0.0)
    min_replicas: int = Field(default=1, ge=1)
    require_ready: bool = True

    @property
    def max_attempts(self) -> int:
        """Deterministic upper bound on poll iterations (always ≥ 1)."""

        return max(1, int(self.timeout_seconds // self.poll_interval_seconds) + 1)


DEFAULT_RECOVERY_CONFIG = RecoveryVerificationConfig()


__all__ = ["DEFAULT_RECOVERY_CONFIG", "RecoveryVerificationConfig"]
