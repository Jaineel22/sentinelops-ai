"""HTTP investigation API (Sub-phases 4E / 4G).

``POST /investigations``                         trigger a manual investigation
``GET  /investigations/{investigation_id}``       investigation state + trace + RCA
``GET  /investigations/{investigation_id}/steps`` just the operational trace
``GET  /incidents/{incident_id}/investigation``   the latest investigation for an incident

Plus ``/health``, ``/ready``, ``/metrics``. The API exposes the structured
operational trace (``InvestigationStep``) — never private model reasoning — and
never a secret.
"""

from __future__ import annotations

from rca_agent.api.routes import investigations_router, system_router
from rca_agent.api.runner import BackgroundInvestigationRunner

__all__ = [
    "BackgroundInvestigationRunner",
    "investigations_router",
    "system_router",
]
