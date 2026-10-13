"""Structured API errors.

Every failure leaves the API as the same JSON envelope, so the frontend has one
shape to render:

    {"error": {"code": "insufficient_stock",
               "message": "Only 2 left of MOS-42.",
               "details": {"sku_id": "MOS-42", "requested": 5, "available": 2},
               "request_id": "..."}}

``code`` is a stable machine-readable identifier; ``message`` is safe to show to
a user; ``details`` carries field-level context. Nothing here ever exposes a
stack trace, a SQL statement or an internal path.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class ApiError(Exception):
    """An error that maps to a documented status code and error code."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}

    def to_payload(self, request_id: str) -> Dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
                "request_id": request_id,
            }
        }


def validation_error(message: str, details: Optional[Dict[str, Any]] = None) -> ApiError:
    return ApiError(422, "validation_error", message, details)


def sku_not_found(sku_id: str) -> ApiError:
    return ApiError(404, "sku_not_found", f"No SKU with id {sku_id!r}.", {"sku_id": sku_id})


def product_not_found(product_id: str) -> ApiError:
    return ApiError(
        404, "product_not_found", f"No product with id {product_id!r}.", {"product_id": product_id}
    )


def insufficient_stock(sku_id: str, requested: int, available: int) -> ApiError:
    if available == 0:
        message = f"{sku_id} is out of stock."
    else:
        message = f"Only {available} left of {sku_id}."
    return ApiError(
        409,
        "insufficient_stock",
        message,
        {"sku_id": sku_id, "requested": requested, "available": available},
    )


def missing_idempotency_key() -> ApiError:
    return ApiError(
        400,
        "idempotency_key_required",
        "POST /api/cart/items requires an Idempotency-Key header.",
    )


def idempotency_key_reuse(key: str) -> ApiError:
    # The same key with a different body is a client bug: replaying it would
    # either silently drop the second request or double-add. Both are wrong.
    return ApiError(
        409,
        "idempotency_key_reuse",
        "This Idempotency-Key was already used with a different request body.",
        {"idempotency_key": key},
    )
