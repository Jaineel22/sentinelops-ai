"""FastAPI routers for the remediation approval + execution + verification API
(Phase 5C/5D/5E/5F).

    POST /remediations                     create a proposal; policy runs
    GET  /remediations                     list, with filters
    GET  /remediations/{id}                one remediation
    GET  /remediations/{id}/audit          append-only audit trail (read-only, 5E)
    POST /remediations/{id}/approve        human APPROVE  (-> APPROVED)
    POST /remediations/{id}/reject         human REJECT   (-> REJECTED)
    POST /remediations/{id}/execute        run an APPROVED remediation (-> EXECUTED |
                                           EXECUTION_FAILED); {"dry_run": true} previews.
    POST /remediations/{id}/verify-recovery  verify actual recovery (5F,
                                           EXECUTED -> VERIFYING -> RECOVERED |
                                           RECOVERY_FAILED); no body fields.

Plus /health, /ready, /metrics. No request body carries a command / script /
executor selector. No real infrastructure is touched — execution is a local
simulation and the verifier only observes.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from remediation_controller.api.schemas import (
    ApprovalRequest,
    AuditEventListResponse,
    AuditEventView,
    CreateRemediationRequest,
    ExecuteRequest,
    ExecutionView,
    RemediationListResponse,
    RemediationView,
    VerificationView,
    VerifyRecoveryRequest,
)
from remediation_controller.domain.enums import (
    ApprovalDecision,
    RemediationActionType,
    RemediationStatus,
)
from remediation_controller.domain.errors import (
    ApprovalError,
    ParameterValidationError,
    UnknownActionError,
    UnknownTargetError,
)
from remediation_controller.domain.proposal import RcaRecommendedActionInput
from remediation_controller.metrics import RemediationMetrics
from remediation_controller.repository import (
    InvalidRemediationStateError,
    ProposalNotMappableError,
    RecoveryVerificationConflictError,
    RemediationExecutionConflictError,
    RemediationExpiredError,
    RemediationFilter,
    RemediationNotFoundError,
    RemediationPolicyBlockedError,
    UnauthorizedApproverError,
)
from remediation_controller.service import RemediationService

logger = logging.getLogger("remediation_controller.api")

system_router = APIRouter(tags=["system"])
remediations_router = APIRouter(prefix="/remediations", tags=["remediations"])


def _service(request: Request) -> RemediationService:
    return request.app.state.service  # type: ignore[no-any-return]


def _metrics(request: Request) -> RemediationMetrics:
    return request.app.state.metrics  # type: ignore[no-any-return]


def _correlation_id(request: Request) -> str | None:
    """A caller-supplied request id, echoed onto the audit trail for correlation.
    Purely informational — never trusted for any decision."""

    value = request.headers.get("x-request-id")
    return value[:64] if value else None


# --- system --------------------------------------------------------
@system_router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@system_router.get("/ready")
async def ready(request: Request) -> Response:
    database = getattr(request.app.state, "database", None)
    db_ok = database is None or await database.ping()

    # Kafka is a best-effort *outbound* lifecycle-event channel (ADR-030); the
    # approval workflow does not depend on it, so its state is reported but does
    # not gate readiness.
    publisher = getattr(request.app.state, "publisher", None)
    kafka_enabled = getattr(request.app.state, "kafka_enabled", False)
    if not kafka_enabled or publisher is None:
        kafka_state = "disabled"
    else:
        kafka_state = "ok" if publisher.ready else "degraded"

    body = {
        "status": "ready" if db_ok else "not-ready",
        "database": "ok" if db_ok else "down",
        "kafka": kafka_state,
    }
    return Response(
        json.dumps(body),
        status_code=status.HTTP_200_OK if db_ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        media_type="application/json",
    )


@system_router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# --- remediations -----------------------------------------------
@remediations_router.post("", response_model=RemediationView, status_code=status.HTTP_201_CREATED)
async def create_remediation(body: CreateRemediationRequest, request: Request) -> RemediationView:
    recommendation = RcaRecommendedActionInput(
        action_type=body.recommended_action.action_type,
        target_service=body.recommended_action.target_service,
        description=body.recommended_action.description,
        rationale=body.recommended_action.rationale,
        evidence_ids=body.recommended_action.evidence_ids,
    )
    try:
        record = await _service(request).propose(
            incident_id=body.incident_id,
            recommendation=recommendation,
            investigation_id=body.investigation_id,
            incident_severity=body.incident_severity,
            target_environment=body.target_environment,
            proposed_by=body.proposed_by,
            correlation_id=_correlation_id(request),
        )
    except ProposalNotMappableError as exc:
        raise HTTPException(422, f"recommendation cannot become a remediation: {exc}") from exc

    _metrics(request).record_proposed(record.status)
    logger.info(
        "remediation created",
        extra={"remediation_id": record.remediation_id, "status": str(record.status)},
    )
    return RemediationView.of(record)


@remediations_router.get("", response_model=RemediationListResponse)
async def list_remediations(
    request: Request,
    incident_id: str | None = None,
    status_: Annotated[RemediationStatus | None, Query(alias="status")] = None,
    action_type: RemediationActionType | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RemediationListResponse:
    records = await _service(request).list(
        RemediationFilter(
            incident_id=incident_id,
            status=status_,
            action_type=action_type,
            limit=limit,
            offset=offset,
        )
    )
    views = [RemediationView.of(r) for r in records]
    return RemediationListResponse(remediations=views, count=len(views))


@remediations_router.get("/{remediation_id}", response_model=RemediationView)
async def get_remediation(remediation_id: str, request: Request) -> RemediationView:
    record = await _service(request).get(remediation_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"remediation {remediation_id!r} not found")
    return RemediationView.of(record)


@remediations_router.get("/{remediation_id}/audit", response_model=AuditEventListResponse)
async def get_remediation_audit(
    remediation_id: str,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditEventListResponse:
    """The remediation's append-only audit trail, oldest event first.

    Read-only by construction: there is no ``POST`` / ``PUT`` / ``PATCH`` /
    ``DELETE`` route for audit events, and the store itself is append-only
    (repository has no mutation path; PostgreSQL rejects UPDATE/DELETE).
    """

    events = await _service(request).list_audit_events(remediation_id, limit=limit, offset=offset)
    if events is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"remediation {remediation_id!r} not found")
    return AuditEventListResponse(
        remediation_id=remediation_id,
        events=[AuditEventView.of(e) for e in events],
        count=len(events),
    )


async def _decide(
    request: Request, remediation_id: str, body: ApprovalRequest, decision: ApprovalDecision
) -> RemediationView:
    try:
        record = await _service(request).decide(
            remediation_id,
            decision=decision,
            approver_identity=body.approver_identity,
            approver_role=body.approver_role,
            reason=body.reason,
            correlation_id=_correlation_id(request),
        )
    except RemediationNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except UnauthorizedApproverError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except (
        InvalidRemediationStateError,
        RemediationExpiredError,
        RemediationPolicyBlockedError,
    ) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    _metrics(request).record_decision(decision)
    logger.info(
        "remediation decision",
        extra={
            "remediation_id": remediation_id,
            "decision": str(decision),
            "new_status": str(record.status),
        },
    )
    return RemediationView.of(record)


@remediations_router.post("/{remediation_id}/approve", response_model=RemediationView)
async def approve_remediation(
    remediation_id: str, body: ApprovalRequest, request: Request
) -> RemediationView:
    return await _decide(request, remediation_id, body, ApprovalDecision.APPROVE)


@remediations_router.post("/{remediation_id}/reject", response_model=RemediationView)
async def reject_remediation(
    remediation_id: str, body: ApprovalRequest, request: Request
) -> RemediationView:
    return await _decide(request, remediation_id, body, ApprovalDecision.REJECT)


@remediations_router.post("/{remediation_id}/execute", response_model=RemediationView)
async def execute_remediation(
    remediation_id: str, body: ExecuteRequest, request: Request
) -> RemediationView:
    """Run an APPROVED remediation through the allow-listed local-simulation
    executor. ``{"dry_run": true}`` previews it (same guards, same executor
    interface) without persisting or mutating anything.

    A genuine executor failure returns ``200`` with ``status=EXECUTION_FAILED``
    and ``execution.status=FAILED`` — it never becomes ``EXECUTED``.
    """

    metrics = _metrics(request)
    try:
        outcome = await _service(request).execute(
            remediation_id, dry_run=body.dry_run, correlation_id=_correlation_id(request)
        )
    except RemediationNotFoundError as exc:
        metrics.record_execution_auth_failure()
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (
        InvalidRemediationStateError,
        RemediationExpiredError,
        RemediationExecutionConflictError,
        ApprovalError,
        UnknownActionError,
        UnknownTargetError,
        ParameterValidationError,
    ) as exc:
        metrics.record_execution_auth_failure()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    metrics.record_execution(dry_run=body.dry_run, outcome=outcome.result.status)
    logger.info(
        "remediation execution requested",
        extra={
            "remediation_id": remediation_id,
            "dry_run": body.dry_run,
            "status": str(outcome.record.status),
            "execution_status": str(outcome.result.status),
        },
    )
    view = RemediationView.of(outcome.record)
    return view.model_copy(update={"execution": ExecutionView.of(outcome.result)})


@remediations_router.post("/{remediation_id}/verify-recovery", response_model=RemediationView)
async def verify_recovery(
    remediation_id: str, body: VerifyRecoveryRequest, request: Request
) -> RemediationView:
    """Verify whether the target system actually recovered after execution
    (Phase 5F). ``EXECUTED -> VERIFYING -> RECOVERED | RECOVERY_FAILED``.

    The body has no fields (``extra="forbid"`` -> ``422`` for any). The verifier
    only **observes** — it runs no command, touches no infrastructure, never
    re-executes the remediation, and never bypasses approval. Safe to retry: a
    repeat on an already-verified remediation returns the stored result
    unchanged (``200``); a repeat while ``VERIFYING`` is a ``409``.

    ``404`` unknown; ``409`` not ``EXECUTED`` / a verification already in
    progress.
    """

    _ = body  # accepted only to reject unknown fields
    try:
        outcome = await _service(request).verify_recovery(
            remediation_id, correlation_id=_correlation_id(request)
        )
    except RemediationNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (InvalidRemediationStateError, RecoveryVerificationConflictError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    logger.info(
        "recovery verification requested",
        extra={
            "remediation_id": remediation_id,
            "status": str(outcome.record.status),
            "verification_status": str(outcome.result.status),
            "replayed": outcome.replayed,
        },
    )
    view = RemediationView.of(outcome.record)
    return view.model_copy(update={"verification": VerificationView.of(outcome.result)})
