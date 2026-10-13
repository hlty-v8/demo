"""Request/response contracts (pydantic v2).

These models are the API contract: FastAPI validates requests against them and
documents them in OpenAPI. Money is always integer ``*_cents``.
"""

from __future__ import annotations

from typing import Dict, List

from pydantic import BaseModel, ConfigDict, Field

# A SKU id is an opaque ASCII marker; the pattern keeps obviously wrong input
# (spaces, control characters, SQL-ish payloads) out before it reaches the DB.
SKU_ID_PATTERN = r"^[A-Za-z0-9._:-]{1,64}$"


class OptionValueOut(BaseModel):
    id: str
    label: str


class OptionDimensionOut(BaseModel):
    id: str
    name: str
    values: List[OptionValueOut]


class SkuOut(BaseModel):
    id: str
    options: Dict[str, str]
    price_cents: int
    available_qty: int
    in_stock: bool
    image_url: str


class ProductOut(BaseModel):
    id: str
    name: str
    description: str
    currency: str
    options: List[OptionDimensionOut]
    skus: List[SkuOut]


class AddCartItemRequest(BaseModel):
    """POST /api/cart/items body.

    ``extra="ignore"`` is deliberate: a client may send ``price`` or
    ``available_qty`` and they are simply not part of the model, so they cannot
    influence anything. The server always re-reads price and stock itself.
    """

    model_config = ConfigDict(extra="ignore")

    sku_id: str = Field(min_length=1, max_length=64, pattern=SKU_ID_PATTERN)
    quantity: int = Field(ge=1, le=10_000)


class CartItemOut(BaseModel):
    sku_id: str
    quantity: int
    unit_price_cents: int
    line_total_cents: int
    available_qty: int
    image_url: str
    options: Dict[str, str]


class CartOut(BaseModel):
    id: str
    items: List[CartItemOut]
    total_items: int
    subtotal_cents: int


class AddCartItemResponse(BaseModel):
    cart_id: str
    item: CartItemOut
    cart: CartOut


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Dict[str, object] = Field(default_factory=dict)
    request_id: str = ""


class ErrorResponse(BaseModel):
    error: ErrorBody
