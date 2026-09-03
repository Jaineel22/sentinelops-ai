"""OpenTelemetry instruments for the remediation-controller (ADR-007 conventions).

Low-cardinality labels only — never ``remediation_id`` / ``incident_id`` on a
metric (those live on spans and structured logs).
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache

from opentelemetry.metrics import Counter, Histogram

from remediation_controller.audit.model import RemediationAuditEvent
from remediation_controller.domain.enums import (
    ApprovalDecision,
    ExecutionStatus,
    RemediationStatus,
)
from sentinelops_common.obs import get_meter


class RemediationMetrics:
    def __init__(self) -> None:
        meter = get_meter()
        self.proposals: Counter = meter.create_counter(
            "remediation.proposals.created",
            unit="1",
            description="remediation proposals created, by resulting status.",
        )
        self.policy_blocks: Counter = meter.create_counter(
            "remediation.policy.blocks",
            unit="1",
            description="proposals the policy engine denied.",
        )
        self.decisions: Counter = meter.create_counter(
            "remediation.approvals",
            unit="1",
            description="human decisions recorded, by decision (APPROVE | REJECT).",
        )
        self.api_requests: Counter = meter.create_counter(
            "remediation.api.requests",
            unit="1",
            description="approval/execution API requests, by route and status class.",
        )
        self.executions: Counter = meter.create_counter(
            "remediation.executions",
            unit="1",
            description="execution attempts, by mode (real | dry_run) and outcome.",
        )
        self.execution_auth_failures: Counter = meter.create_counter(
            "remediation.execution.authorization_failures",
            unit="1",
            description="execution requests rejected before reaching the executor.",
        )
        self.audit_events_written: Counter = meter.create_counter(
            "remediation.audit.events_written",
            unit="1",
            description="append-only audit events committed, by event type.",
        )
        self.audit_write_failures: Counter = meter.create_counter(
            "remediation.audit.write_failures",
            unit="1",
            description=(
                "lifecycle operations that failed before their audit events "
                "could be committed (the whole transaction rolled back)."
            ),
        )
        self.verifications: Counter = meter.create_counter(
            "remediation.recovery.verifications",
            unit="1",
            description="recovery verifications completed, by outcome.",
        )
        self.verification_failures: Counter = meter.create_counter(
            "remediation.recovery.verification_failures",
            unit="1",
            description="recovery verifications that ended RECOVERY_FAILED.",
        )
        self.verification_duration: Histogram = meter.create_histogram(
            "remediation.recovery.verification_duration",
            unit="s",
            description="(simulated) wall-clock time spent in one recovery verification.",
        )
        # --- Phase 5G: Kafka lifecycle events ---
        self.events_published: Counter = meter.create_counter(
            "remediation.events.published",
            unit="1",
            description="remediation lifecycle events published to Kafka, by event type.",
        )
        self.event_publish_failures: Counter = meter.create_counter(
            "remediation.events.publish_failures",
            unit="1",
            description=(
                "lifecycle events that could not be published after their "
                "transition committed (the audit trail still holds the record)."
            ),
        )
        self.event_publish_latency: Histogram = meter.create_histogram(
            "remediation.events.publish_latency",
            unit="s",
            description="time to publish one remediation lifecycle event to Kafka.",
        )

    def record_proposed(self, result_status: RemediationStatus) -> None:
        self.proposals.add(1, {"status": str(result_status)})
        if result_status is RemediationStatus.BLOCKED:
            self.policy_blocks.add(1)

    def record_decision(self, decision: ApprovalDecision) -> None:
        self.decisions.add(1, {"decision": str(decision)})

    def record_execution(self, *, dry_run: bool, outcome: ExecutionStatus) -> None:
        self.executions.add(
            1,
            {"mode": "dry_run" if dry_run else "real", "outcome": str(outcome).lower()},
        )

    def record_execution_auth_failure(self) -> None:
        self.execution_auth_failures.add(1)

    def record_audit_events(self, events: Iterable[RemediationAuditEvent]) -> None:
        for event in events:
            self.audit_events_written.add(1, {"event_type": str(event.event_type)})

    def record_audit_write_failure(self) -> None:
        self.audit_write_failures.add(1)

    def record_verification(
        self, final_status: RemediationStatus, *, duration_seconds: float
    ) -> None:
        outcome = "recovered" if final_status is RemediationStatus.RECOVERED else "recovery_failed"
        self.verifications.add(1, {"outcome": outcome})
        if final_status is RemediationStatus.RECOVERY_FAILED:
            self.verification_failures.add(1)
        self.verification_duration.record(max(0.0, duration_seconds), {"outcome": outcome})

    def record_event_published(self, event_type: str, *, duration_seconds: float) -> None:
        self.events_published.add(1, {"event_type": event_type})
        self.event_publish_latency.record(max(0.0, duration_seconds), {"event_type": event_type})

    def record_event_publish_failure(self, event_type: str) -> None:
        self.event_publish_failures.add(1, {"event_type": event_type})


@lru_cache
def get_metrics() -> RemediationMetrics:
    return RemediationMetrics()
