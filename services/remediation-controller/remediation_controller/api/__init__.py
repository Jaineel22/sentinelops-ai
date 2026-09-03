"""HTTP approval + execution + audit + recovery-verification API (Phase 5C-5F).

``POST /remediations``                     create a proposal (policy runs)
``GET  /remediations``                     list, with filters
``GET  /remediations/{id}``                one remediation
``GET  /remediations/{id}/audit``          the append-only audit trail (read-only, 5E)
``POST /remediations/{id}/approve``        human APPROVE
``POST /remediations/{id}/reject``         human REJECT
``POST /remediations/{id}/execute``        run an APPROVED remediation (5D)
``POST /remediations/{id}/verify-recovery`` verify actual recovery (5F)

Plus ``/health``, ``/ready``, ``/metrics``. The audit trail is read-only. The
recovery verifier only observes — no request body carries a command.
"""

from __future__ import annotations

from remediation_controller.api.routes import remediations_router, system_router

__all__ = ["remediations_router", "system_router"]
