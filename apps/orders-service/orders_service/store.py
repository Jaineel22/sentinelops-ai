"""In-memory order store.

Phase 1 has no database (that arrives with the incident store in Phase 3). This
bounded map just lets ``GET /orders/{id}`` return something real. It is
per-process and lost on restart — by design for now.
"""

from __future__ import annotations

from collections import OrderedDict

from orders_service.domain import Order


class OrderStore:
    def __init__(self, max_size: int = 1000) -> None:
        self._orders: OrderedDict[str, Order] = OrderedDict()
        self._max_size = max_size

    def add(self, order: Order) -> None:
        self._orders[order.order_id] = order
        self._orders.move_to_end(order.order_id)
        while len(self._orders) > self._max_size:
            self._orders.popitem(last=False)

    def get(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def __len__(self) -> int:
        return len(self._orders)
