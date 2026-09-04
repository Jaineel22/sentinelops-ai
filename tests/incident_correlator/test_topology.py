"""Pure topology / cross-service correlation logic (Phase 8).

No DB, no Kafka — :mod:`incident_correlator.topology` is a pure module over the
static service-dependency graph.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from incident_correlator.domain import (
    Incident,
    IncidentStatus,
    Severity,
    correlation_key,
)
from incident_correlator.topology import (
    SERVICE_DEPENDENCY_GRAPH,
    ServiceDependency,
    TopologyConfig,
    correlate_incidents,
    dependencies_of,
    dependents_of,
    find_related_incidents,
    incidents_overlap,
    related_services,
)

_BASE = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


def _incident(
    incident_id: str,
    service: str,
    *,
    environment: str = "development",
    start_offset: float = 0.0,
    duration: float = 30.0,
    status: IncidentStatus = IncidentStatus.OPEN,
) -> Incident:
    started = _BASE + timedelta(seconds=start_offset)
    last = started + timedelta(seconds=duration)
    return Incident(
        id=incident_id,
        correlation_key=correlation_key(service, environment),
        service=service,
        environment=environment,
        status=status,
        severity=Severity.HIGH,
        severity_reasons=[],
        title=f"{service} incident",
        anomaly_count=1,
        max_anomaly_score=0.9,
        max_error_rate=0.2,
        max_latency_p95_ms=800.0,
        detector="isolation_forest",
        started_at=started,
        last_evidence_at=last,
        created_at=started,
        updated_at=last,
    )


# --- graph helpers ---------------------------------------------------
def test_service_dependencies() -> None:
    assert dependencies_of("orders-service") == ["payments-service", "inventory-service"]
    assert dependencies_of("payments-service") == []
    assert dependents_of("payments-service") == ["orders-service"]
    assert dependents_of("inventory-service") == ["orders-service"]
    assert related_services("orders-service") == {"payments-service", "inventory-service"}
    assert related_services("payments-service") == {"orders-service"}
    assert related_services("unrelated-service") == set()


def test_service_dependency_dataclass_and_config_defaults() -> None:
    dep = ServiceDependency(service="orders-service", depends_on=["payments-service"])
    assert dep.environment == "*"
    assert dep.depends_on == ["payments-service"]

    cfg = TopologyConfig()
    assert cfg.correlation_window_seconds == 600.0
    assert cfg.dependency_graph == SERVICE_DEPENDENCY_GRAPH
    assert {d.service for d in cfg.dependencies()} == {"orders-service"}

    custom = TopologyConfig(dependency_graph={"a": ["b"]})
    assert dependencies_of("a", custom.dependency_graph) == ["b"]
    assert dependents_of("b", custom.dependency_graph) == ["a"]


def test_incidents_overlap_respects_window() -> None:
    a = _incident("a", "orders-service", start_offset=0, duration=30)
    near = _incident("b", "payments-service", start_offset=120, duration=30)
    far = _incident("c", "payments-service", start_offset=1200, duration=30)
    assert incidents_overlap(a, near, 600) is True
    assert incidents_overlap(a, far, 600) is False


# --- find_related_incidents ----------------------------------------
def test_find_related_incidents() -> None:
    orders = _incident("inc_orders", "orders-service", start_offset=0)
    payments = _incident("inc_payments", "payments-service", start_offset=60)
    inventory = _incident("inc_inventory", "inventory-service", start_offset=90)
    unrelated = _incident("inc_other", "shipping-service", start_offset=30)

    related = find_related_incidents(
        orders, [orders, payments, inventory, unrelated], time_window_seconds=600
    )
    assert set(related) == {"inc_payments", "inc_inventory"}

    # the reverse direction works too: payments sees its upstream caller
    assert find_related_incidents(payments, [orders, payments], time_window_seconds=600) == [
        "inc_orders"
    ]


def test_find_related_incidents_is_environment_scoped() -> None:
    orders = _incident("inc_orders", "orders-service", environment="production")
    payments = _incident("inc_payments", "payments-service", environment="staging")
    assert find_related_incidents(orders, [orders, payments], time_window_seconds=600) == []


def test_find_related_incidents_outside_window_excluded() -> None:
    orders = _incident("inc_orders", "orders-service", start_offset=0, duration=20)
    payments = _incident("inc_payments", "payments-service", start_offset=5000)
    assert find_related_incidents(orders, [orders, payments], time_window_seconds=600) == []


# --- correlate_incidents ------------------------------------------
def test_correlate_incidents() -> None:
    orders = _incident("inc_orders", "orders-service", start_offset=0)
    payments = _incident("inc_payments", "payments-service", start_offset=60)
    inventory = _incident("inc_inventory", "inventory-service", start_offset=90)

    links = correlate_incidents([payments, orders, inventory], time_window_seconds=600)
    assert [(c.incident_id, c.related_incident_id) for c in links] == [
        ("inc_orders", "inc_inventory"),
        ("inc_orders", "inc_payments"),
    ]
    assert all(c.relation_type == "dependency" for c in links)
    assert "depends on" in links[0].reason


def test_correlate_incidents_no_edges_when_only_one_service() -> None:
    a = _incident("a", "payments-service", start_offset=0)
    b = _incident("b", "inventory-service", start_offset=10)
    # payments and inventory are siblings, not a declared dependency edge
    assert correlate_incidents([a, b], time_window_seconds=600) == []


def test_cross_service_correlation_window() -> None:
    orders = _incident("inc_orders", "orders-service", start_offset=0, duration=20)
    payments_in = _incident("inc_pay_in", "payments-service", start_offset=300)
    payments_out = _incident("inc_pay_out", "payments-service", start_offset=3600)

    linked = correlate_incidents([orders, payments_in], time_window_seconds=600)
    assert [c.related_incident_id for c in linked] == ["inc_pay_in"]

    not_linked = correlate_incidents([orders, payments_out], time_window_seconds=600)
    assert not_linked == []
