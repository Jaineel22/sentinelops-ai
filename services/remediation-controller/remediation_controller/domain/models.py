"""Shared value objects: identifiers, the structural target model + allow-list,
and the human-approval record.

Design rules (spec sections 3, 4, 5):

* A target is **structured**, never a free string that could carry a command.
* The set of valid target services is a closed, code-defined allow-list.
* An approval always carries a non-empty approver identity.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from remediation_controller.domain.enums import ApprovalDecision, ApproverRole, TargetType
from remediation_controller.domain.errors import UnknownTargetError

# --- identifiers ---------------------------------------------------------
# Match the id shapes established by earlier phases so cross-references stay
# checkable: Phase 3 ``inc_<hex>`` and Phase 4 ``rca_<16 hex>``.
INCIDENT_ID_RE = r"^inc_[0-9a-f]{6,32}$"
INVESTIGATION_ID_RE = r"^rca_[0-9a-f]{16}$"
REMEDIATION_ID_RE = r"^rem_[0-9a-f]{16}$"
APPROVAL_ID_RE = r"^apr_[0-9a-f]{16}$"


def new_remediation_id() -> str:
    return f"rem_{secrets.token_hex(8)}"


def new_approval_id() -> str:
    return f"apr_{secrets.token_hex(8)}"


# --- target model + allow-list -----------------------------------------
# Only services that actually exist in this repository belong here. The
# controller can never target anything not on this list (spec section 3).
ALLOWED_TARGET_SERVICES: frozenset[str] = frozenset({"orders-service"})
ALLOWED_ENVIRONMENTS: frozenset[str] = frozenset({"development", "staging", "production"})

_SERVICE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$")


class ServiceTarget(BaseModel):
    """A structured remediation target — a named service in a named environment.

    ``service_name`` is constrained to a strict slug pattern; there is no way to
    smuggle a shell fragment, path, or URL through it. Membership in
    :data:`ALLOWED_TARGET_SERVICES` is checked separately by
    :func:`resolve_target` / :func:`is_allowed_service` so the value object stays
    reusable while the allow-list decision stays explicit.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_type: TargetType = TargetType.SERVICE
    service_name: str = Field(min_length=2, max_length=63)
    environment: str

    @field_validator("target_type")
    @classmethod
    def _only_service(cls, value: TargetType) -> TargetType:
        if value is not TargetType.SERVICE:
            raise ValueError("only SERVICE targets are supported in Phase 5")
        return value

    @field_validator("service_name")
    @classmethod
    def _valid_slug(cls, value: str) -> str:
        if not _SERVICE_NAME_RE.match(value):
            raise ValueError(f"service_name {value!r} is not a valid service slug")
        return value

    @field_validator("environment")
    @classmethod
    def _known_environment(cls, value: str) -> str:
        if value not in ALLOWED_ENVIRONMENTS:
            raise ValueError(f"environment {value!r} is not one of {sorted(ALLOWED_ENVIRONMENTS)}")
        return value

    def __str__(self) -> str:
        return f"{self.service_name}:{self.environment}"


def is_allowed_service(service_name: str) -> bool:
    """Deterministic allow-list membership check. Fails closed."""

    return service_name in ALLOWED_TARGET_SERVICES


def resolve_target(target: ServiceTarget) -> ServiceTarget:
    """Return ``target`` iff its service is on the allow-list, else raise.

    The single choke point every executable code path must go through before it
    treats a target as real.
    """

    if not is_allowed_service(target.service_name):
        raise UnknownTargetError(
            f"target service {target.service_name!r} is not on the remediation allow-list "
            f"({sorted(ALLOWED_TARGET_SERVICES)})"
        )
    return target


# --- human approval record -------------------------------------------
class RemediationApproval(BaseModel):
    """A recorded human decision on a remediation proposal (spec section 5).

    Phase 5A establishes the model and its validation rules; the approval API and
    role-authorization matrix are Sub-phase 5C. An approval is immutable and can
    never be constructed without a non-empty approver identity.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: str = Field(pattern=APPROVAL_ID_RE)
    remediation_id: str = Field(pattern=REMEDIATION_ID_RE)
    decision: ApprovalDecision
    approver_identity: str = Field(min_length=1, max_length=128)
    approver_role: ApproverRole
    reason: str = Field(default="", max_length=1000)
    decided_at: datetime

    @field_validator("approver_identity")
    @classmethod
    def _identity_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("approver_identity must not be empty or whitespace")
        return value
