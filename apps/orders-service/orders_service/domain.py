"""Order domain model and request/response contracts.

The business logic is intentionally thin: the point of this service is the
*operational activity* it produces, not order management.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_serializer

SUPPORTED_CURRENCIES = ("INR", "USD", "EUR", "GBP")
Currency = Literal["INR", "USD", "EUR", "GBP"]

Amount = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]


def new_order_id() -> str:
    """Short, URL-safe, collision-resistant order identifier."""

    return f"ord_{secrets.token_hex(8)}"


class CreateOrderRequest(BaseModel):
    customer_id: str = Field(min_length=1, max_length=64)
    amount: Amount
    currency: Currency = "INR"


class Order(BaseModel):
    order_id: str
    customer_id: str
    amount: Amount
    currency: Currency
    status: Literal["created"] = "created"
    created_at: datetime

    @field_serializer("amount")
    def _serialize_amount(self, value: Decimal) -> str:
        # Money as a fixed-precision string avoids float rounding surprises for
        # consumers in any language.
        return f"{value:.2f}"

    @classmethod
    def create(cls, request: CreateOrderRequest) -> Order:
        return cls(
            order_id=new_order_id(),
            customer_id=request.customer_id,
            amount=request.amount,
            currency=request.currency,
            created_at=datetime.now(tz=UTC),
        )


class CreateOrderResponse(BaseModel):
    order_id: str
    status: Literal["created"]
