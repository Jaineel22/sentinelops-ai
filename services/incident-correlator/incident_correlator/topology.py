"""Cross-service incident correlation via a static service-dependency graph (Phase 8).

Phase 3 correlates anomalies for one ``service:environment`` into a single
incident (:mod:`incident_correlator.correlation`). Phase 8 adds the next layer:
when a service and one of its **declared dependencies** are on fire at the same
time, link their incidents so an operator (and the Phase 4 RCA agent) sees the
blast radius instead of triaging each incident in isolation.

Design rules (consistent with ADR-015 — deterministic, explainable correlation):

* The dependency graph is **static and explicit**. No topology discovery, no
  trace-derived edges — those would make the linking non-reproducible. The
  default graph is ``{"orders-service": ["payments-service", "inventory-service"]}``;
  override it with the ``SERVICE_DEPENDENCY_GRAPH`` env var (JSON).
* Linking is **directional**: the edge is stored from the *dependent* incident to
  its *dependency* (``orders-service`` -> ``payments-service``). The relation
  graph is therefore acyclic whenever the dependency graph is.
* Two incidents are related only if their active intervals **overlap within**
  ``CROSS_SERVICE_CORRELATION_WINDOW_SECONDS`` (default 600 s) *and* they share an
  ``environment``.

Complexity: :func:`correlate_incidents` is ``O(n * d)`` — for each incident it
looks only at incidents in its graph-adjacent services (``d`` of them, bounded by
one active incident per service), never all pairs.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from incident_correlator.domain import Incident

# The default topology. ``orders-service`` calls ``payments-service`` and
# ``inventory-service`` synchronously (see apps/orders-service), so a failure in
# either propagates upward as an orders-service anomaly.
SERVICE_DEPENDENCY_GRAPH: dict[str, list[str]] = {
    "orders-service": ["payments-service", "inventory-service"],
}


class IncidentRelationDirection(StrEnum):
    """Which end of a stored relation an incident sits on."""

    DEPENDENT = "dependent"
    DEPENDENCY = "dependency"


@dataclass(frozen=True)
class ServiceDependency:
    """One node of the dependency graph: ``service`` depends on ``depends_on``."""

    service: str
    depends_on: list[str] = field(default_factory=list)
    environment: str = "*"


class TopologyConfig(BaseSettings):
    """Cross-service correlation settings (``CROSS_SERVICE_*`` env vars).

    The dependency graph itself is read from ``SERVICE_DEPENDENCY_GRAPH`` (JSON)
    so it keeps the name used everywhere else in the docs and ``.env``.
    """

    model_config = SettingsConfigDict(
        env_prefix="CROSS_SERVICE_",
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    correlation_window_seconds: float = 600.0
    dependency_graph: dict[str, list[str]] = Field(
        default_factory=lambda: {k: list(v) for k, v in SERVICE_DEPENDENCY_GRAPH.items()},
        validation_alias="SERVICE_DEPENDENCY_GRAPH",
    )

    def dependencies(self) -> list[ServiceDependency]:
        return [
            ServiceDependency(service=svc, depends_on=list(deps))
            for svc, deps in self.dependency_graph.items()
        ]


# --- pure graph helpers ------------------------------------------------
def _graph(graph: dict[str, list[str]] | None) -> dict[str, list[str]]:
    return SERVICE_DEPENDENCY_GRAPH if graph is None else graph


def dependencies_of(service: str, graph: dict[str, list[str]] | None = None) -> list[str]:
    """Services that ``service`` depends on (downstream)."""

    return list(_graph(graph).get(service, []))


def dependents_of(service: str, graph: dict[str, list[str]] | None = None) -> list[str]:
    """Services that depend on ``service`` (upstream callers)."""

    return sorted(svc for svc, deps in _graph(graph).items() if service in deps)


def related_services(service: str, graph: dict[str, list[str]] | None = None) -> set[str]:
    """Every service adjacent to ``service`` in the dependency graph, either way."""

    return set(dependencies_of(service, graph)) | set(dependents_of(service, graph))


# --- interval overlap ------------------------------------------------
def incidents_overlap(a: Incident, b: Incident, window_seconds: float) -> bool:
    """True when the two incidents were both active within ``window_seconds``.

    Each incident spans ``[started_at, last_evidence_at]``; the two are considered
    concurrent if those intervals are within ``window_seconds`` of each other
    (a strict overlap plus a tolerance for slightly staggered onsets).
    """

    slack = timedelta(seconds=window_seconds)
    return a.started_at <= b.last_evidence_at + slack and b.started_at <= a.last_evidence_at + slack


@dataclass(frozen=True)
class IncidentCorrelation:
    """A directed link discovered between two incidents in adjacent services."""

    incident_id: str  # the dependent incident (upstream caller)
    related_incident_id: str  # the dependency incident (downstream)
    relation_type: str
    reason: str


def _order_edge(
    incident: Incident, other: Incident, graph: dict[str, list[str]] | None
) -> tuple[Incident, Incident]:
    """Return ``(dependent, dependency)`` for the pair, per the graph."""

    if other.service in dependencies_of(incident.service, graph):
        return incident, other
    return other, incident


def find_related_incidents(
    incident: Incident,
    incidents: Iterable[Incident],
    time_window_seconds: float,
    graph: dict[str, list[str]] | None = None,
) -> list[str]:
    """IDs of incidents adjacent to ``incident`` in the graph and concurrent with it.

    Same-environment only; ``incident`` itself is never returned.
    """

    neighbours = related_services(incident.service, graph)
    if not neighbours:
        return []
    return [
        other.id
        for other in incidents
        if other.id != incident.id
        and other.service in neighbours
        and other.environment == incident.environment
        and incidents_overlap(incident, other, time_window_seconds)
    ]


def correlate_incidents(
    incidents: Iterable[Incident],
    time_window_seconds: float,
    graph: dict[str, list[str]] | None = None,
) -> list[IncidentCorrelation]:
    """Group a set of incidents into directed dependency links.

    One :class:`IncidentCorrelation` per ``(dependent, dependency)`` edge whose
    incidents share an environment and overlap within ``time_window_seconds``.
    Deterministic: the output is sorted by ``(incident_id, related_incident_id)``.
    """

    by_service: dict[str, list[Incident]] = {}
    for inc in incidents:
        by_service.setdefault(inc.service, []).append(inc)

    seen: set[tuple[str, str]] = set()
    links: list[IncidentCorrelation] = []
    for inc in (i for group in by_service.values() for i in group):
        for dep_service in dependencies_of(inc.service, graph):
            for other in by_service.get(dep_service, []):
                if other.environment != inc.environment:
                    continue
                if not incidents_overlap(inc, other, time_window_seconds):
                    continue
                dependent, dependency = _order_edge(inc, other, graph)
                key = (dependent.id, dependency.id)
                if key in seen:
                    continue
                seen.add(key)
                links.append(
                    IncidentCorrelation(
                        incident_id=dependent.id,
                        related_incident_id=dependency.id,
                        relation_type="dependency",
                        reason=(
                            f"{dependent.service} depends on {dependency.service}; "
                            f"both had active incidents within {time_window_seconds:.0f}s"
                        ),
                    )
                )
    links.sort(key=lambda c: (c.incident_id, c.related_incident_id))
    return links


__all__ = [
    "SERVICE_DEPENDENCY_GRAPH",
    "IncidentCorrelation",
    "IncidentRelationDirection",
    "ServiceDependency",
    "TopologyConfig",
    "correlate_incidents",
    "dependencies_of",
    "dependents_of",
    "find_related_incidents",
    "incidents_overlap",
    "related_services",
]
