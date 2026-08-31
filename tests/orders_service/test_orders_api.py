"""POST /orders and GET /orders/{id} behaviour."""

from __future__ import annotations

from fastapi.testclient import TestClient

from orders_service.kafka_producer import InMemoryEventPublisher

VALID = {"customer_id": "customer-123", "amount": 1499.00, "currency": "INR"}


def test_create_order_succeeds_and_publishes_one_event(
    client: TestClient, publisher: InMemoryEventPublisher
) -> None:
    response = client.post("/orders", json=VALID)

    assert response.status_code == 201
    body = response.json()
    assert body["order_id"].startswith("ord_")
    assert body["status"] == "created"
    assert len(publisher.published) == 1


def test_created_order_is_retrievable(client: TestClient) -> None:
    order_id = client.post("/orders", json=VALID).json()["order_id"]

    response = client.get(f"/orders/{order_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["order_id"] == order_id
    assert body["amount"] == "1499.00"
    assert body["currency"] == "INR"
    assert body["status"] == "created"


def test_unknown_order_returns_404(client: TestClient) -> None:
    assert client.get("/orders/ord_does_not_exist").status_code == 404


def test_missing_customer_id_is_rejected(client: TestClient) -> None:
    payload = {"amount": 10, "currency": "INR"}
    assert client.post("/orders", json=payload).status_code == 422


def test_non_positive_amount_is_rejected(client: TestClient) -> None:
    assert client.post("/orders", json={**VALID, "amount": 0}).status_code == 422


def test_unsupported_currency_is_rejected(client: TestClient) -> None:
    assert client.post("/orders", json={**VALID, "currency": "JPY"}).status_code == 422


def test_amount_precision_is_normalised_to_two_places(
    client: TestClient, publisher: InMemoryEventPublisher
) -> None:
    client.post("/orders", json={**VALID, "amount": 12.5})
    envelope, _key, _headers = publisher.published[0]
    assert envelope.payload["amount"] == "12.50"


def test_publish_failure_returns_503(client: TestClient, publisher: InMemoryEventPublisher) -> None:
    publisher.fail_next = True

    response = client.post("/orders", json=VALID)

    assert response.status_code == 503
    assert publisher.published == []
