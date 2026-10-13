"""Endpoint-level tests that do not need an ASGI transport.

Why this module exists: the HTTP tests (test_cart.py, test_idempotency.py, ...)
drive the app through Starlette's ``TestClient``, which needs an asyncio event
loop. On a machine where loopback connections are blocked that is impossible, so
the *bodies* of the endpoints - cookie issuance, Idempotency-Key validation,
error mapping - would otherwise never be exercised anywhere.

Here the endpoint callables are taken straight off the router and called with
real Starlette ``Request``/``Response`` objects. What is *not* covered by design
is the ASGI transport itself, FastAPI's request validation, and the exception
handlers; those are covered by the skipped HTTP modules and by test_contract.py.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, Optional, Tuple

import pytest
from starlette.requests import Request
from starlette.responses import Response

from app.errors import ApiError
from app.main import create_app
from app.models import AddCartItemRequest


def endpoint_for(app: Any, path: str, method: str) -> Callable[..., Any]:
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"no {method} {path} route registered")


def make_request(
    app: Any,
    path: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Dict[str, str]] = None,
) -> Request:
    raw_headers: list[Tuple[bytes, bytes]] = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    if cookies:
        joined = "; ".join(f"{name}={value}" for name, value in cookies.items())
        raw_headers.append((b"cookie", joined.encode("latin-1")))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": b"",
        "root_path": "",
        "headers": raw_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "app": app,
        "state": {},
    }
    return Request(scope)


def set_cookie_header(response: Response) -> str:
    return response.headers.get("set-cookie", "")


def add_item(
    app: Any,
    *,
    sku_id: str = "EMB-38",
    quantity: int = 1,
    key: Optional[str] = "endpoint-key",
    cookies: Optional[Dict[str, str]] = None,
):
    endpoint = endpoint_for(app, "/api/cart/items", "POST")
    response = Response()
    request = make_request(app, "/api/cart/items", "POST", cookies=cookies)
    payload = AddCartItemRequest(sku_id=sku_id, quantity=quantity)
    body = endpoint(payload, request, response, idempotency_key=key)
    return response, body


@pytest.fixture()
def app(db_path: str):
    return create_app(db_path)


def test_get_cart_issues_a_cart_cookie(app):
    endpoint = endpoint_for(app, "/api/cart", "GET")
    response = Response()
    body = endpoint(make_request(app, "/api/cart"), response)

    assert body["items"] == []
    assert body["total_items"] == 0
    cookie = set_cookie_header(response)
    assert "pdp_cart_id=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie or "samesite=lax" in cookie
    assert cookie.split("pdp_cart_id=")[1].split(";")[0] == body["id"]


def test_get_cart_keeps_an_existing_cart_and_does_not_reset_the_cookie(app):
    endpoint = endpoint_for(app, "/api/cart", "GET")
    response = Response()
    body = endpoint(make_request(app, "/api/cart", cookies={"pdp_cart_id": "known-cart-1234"}), response)

    assert body["id"] == "known-cart-1234"
    assert set_cookie_header(response) == ""


def test_missing_idempotency_key_is_rejected_with_the_documented_error(app):
    with pytest.raises(ApiError) as caught:
        add_item(app, key=None)

    assert caught.value.status_code == 400
    assert caught.value.code == "idempotency_key_required"
    payload = caught.value.to_payload("req-42")
    assert payload["error"] == {
        "code": "idempotency_key_required",
        "message": "POST /api/cart/items requires an Idempotency-Key header.",
        "details": {},
        "request_id": "req-42",
    }


def test_blank_idempotency_key_is_rejected(app):
    with pytest.raises(ApiError) as caught:
        add_item(app, key="   ")
    assert caught.value.code == "idempotency_key_required"


def test_overlong_idempotency_key_is_rejected(app):
    with pytest.raises(ApiError) as caught:
        add_item(app, key="x" * 256)
    assert caught.value.status_code == 422
    assert caught.value.code == "validation_error"


def test_add_item_returns_201_and_a_cart_cookie(app):
    response, body = add_item(app, sku_id="EMB-38", quantity=2, key="endpoint-1")

    assert response.status_code == 201
    assert body["item"]["sku_id"] == "EMB-38"
    assert body["item"]["quantity"] == 2
    assert body["cart"]["total_items"] == 2
    cookie = set_cookie_header(response)
    assert body["cart_id"] in cookie


def test_replayed_request_returns_the_same_status_and_body(app):
    first_response, first_body = add_item(app, quantity=1, key="endpoint-replay")
    second_response, second_body = add_item(app, quantity=1, key="endpoint-replay")

    assert first_response.status_code == second_response.status_code == 201
    assert first_body == second_body


def test_unknown_sku_raises_a_structured_404(app):
    with pytest.raises(ApiError) as caught:
        add_item(app, sku_id="NOPE-99", key="endpoint-missing")

    assert caught.value.status_code == 404
    assert caught.value.details["sku_id"] == "NOPE-99"


def test_unknown_product_raises_a_structured_404(app):
    endpoint = endpoint_for(app, "/api/products/{product_id}", "GET")
    with pytest.raises(ApiError) as caught:
        endpoint("no-such-product")

    assert caught.value.status_code == 404
    assert caught.value.code == "product_not_found"


def test_product_endpoint_returns_the_catalogue(app):
    endpoint = endpoint_for(app, "/api/products/{product_id}", "GET")
    product = endpoint("aurora-trail-runner")

    assert product["id"] == "aurora-trail-runner"
    assert [dimension["id"] for dimension in product["options"]] == ["color", "size"]
    assert len(product["skus"]) >= 6


def test_sku_image_endpoint_serves_svg(app):
    endpoint = endpoint_for(app, "/api/skus/{sku_id}/image", "GET")
    response = endpoint("EMB-38")

    assert response.media_type == "image/svg+xml"
    assert ">38<" in response.body.decode("utf-8")


def test_sku_image_endpoint_rejects_an_unknown_sku(app):
    endpoint = endpoint_for(app, "/api/skus/{sku_id}/image", "GET")
    with pytest.raises(ApiError) as caught:
        endpoint("NOPE-99")
    assert caught.value.code == "sku_not_found"
