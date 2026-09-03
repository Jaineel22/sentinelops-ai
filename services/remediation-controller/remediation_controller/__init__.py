"""remediation-controller — Phase 5: human-approved remediation.

Phase 4 (rca-agent) produces an evidence-grounded ``RCAReport`` whose
``recommended_action`` is a *recommendation for a human*, drawn from a closed
enum, with no field for an executable command. Phase 5 owns the boundary between
that recommendation and an actual operational action:

    AI recommendation
        -> deterministic mapping onto a CLOSED action catalogue
        -> policy validation          (Sub-phase 5B)
        -> human approval             (Sub-phase 5C)
        -> allow-listed execution     (Sub-phase 5D, local simulation first)
        -> audit trail                (Sub-phase 5E)
        -> recovery verification      (Sub-phase 5F)

**AI recommendation != execution authority.** The rca-agent never gains the
ability to act; this service turns a recommendation into *intent* that a human
must approve, and can only ever express one of a small set of pre-defined,
parameter-bounded actions against an allow-listed target.

Sub-phase 5A (this module) is the domain foundation only: the closed action
catalogue, the structural remediation-proposal model, the lifecycle state
machine, the approval model, and the deterministic RCA-recommendation -> proposal
mapping. There is **no executor, no database, no API and no Kafka wiring here** —
those arrive in later sub-phases.
"""

from __future__ import annotations

__version__ = "0.5.0"
SERVICE_NAME = "remediation-controller"
