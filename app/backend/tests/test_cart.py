"""Cart endpoint tests: contract, server-side validation, stock rules, errors."""

from __future__ import annotations

from typing import Any, Dict

from app.catalog import PRODUCT

PRODUCT_URL = f"/api/products/{PRODUCT['id']}"


def add_item(client, sku_id: str = "EMB-38", quantity: int = 1, key: str = "key-1"):
    return client.post(
        "/api/cart/items",
        json={"sku_id": sku_id, "quantity": quantity},
        headers={"Idempotency-Key": key},
    )


def stock_of(client, sku_id: str) -> int:
    skus = client.get(PRODUCT_URL).json()["skus"]
    return next(sku["available_qty"] for sku in skus if sku["id"] == sku_id)


def price_of(client, sku_id: str) -> int:
    skus = client.get(PRODUCT_URL).json()["skus"]
    return next(sku["price_cents"] for sku in skus if sku["id"] == sku_id)


def test_cart_starts_empty_and_issues_a_cart_cookie(client):
    response = client.get("/api/cart")
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total_items"] == 0
    assert body["subtotal_cents"] == 0
    assert body["id"]
    assert response.cookies.get("pdp_cart_id")


def test_add_item_success_contract(client):
    unit_price = price_of(client, "EMB-38")
    response = add_item(client, quantity=2)

    assert response.status_code == 201
    body = response.json()
    assert body["item"]["sku_id"] == "EMB-38"
    assert body["item"]["quantity"] == 2
    assert body["item"]["unit_price_cents"] == unit_price
    assert body["item"]["line_total_cents"] == unit_price * 2
    assert body["item"]["options"] == {"color": "ember", "size": "38"}
    assert body["cart"]["total_items"] == 2
    assert body["cart"]["subtotal_cents"] == unit_price * 2
    assert body["cart"]["id"] == body["cart_id"]


def test_cart_is_readable_after_adding(client):
    add_item(client, quantity=1)
    cart = client.get("/api/cart").json()
    assert cart["total_items"] == 1
    assert [item["sku_id"] for item in cart["items"]] == ["EMB-38"]


def test_adding_the_same_sku_twice_accumulates_one_line(client):
    add_item(client, quantity=1, key="a")
    add_item(client, quantity=2, key="b")
    cart = client.get("/api/cart").json()
    assert len(cart["items"]) == 1
    assert cart["items"][0]["quantity"] == 3
    assert cart["total_items"] == 3


def test_stock_is_decremented_on_add(client):
    before = stock_of(client, "EMB-38")
    add_item(client, quantity=2)
    assert stock_of(client, "EMB-38") == before - 2


def test_missing_idempotency_key_is_rejected(client):
    response = client.post("/api/cart/items", json={"sku_id": "EMB-38", "quantity": 1})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "idempotency_key_required"


def test_blank_idempotency_key_is_rejected(client):
    response = client.post(
        "/api/cart/items",
        json={"sku_id": "EMB-38", "quantity": 1},
        headers={"Idempotency-Key": "   "},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "idempotency_key_required"


def test_overlong_idempotency_key_is_rejected(client):
    response = add_item(client, key="x" * 256)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_unknown_sku_is_rejected(client):
    response = add_item(client, sku_id="NOPE-99")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "sku_not_found"
    assert body["error"]["details"]["sku_id"] == "NOPE-99"


def test_non_ascii_sku_id_fails_validation_before_touching_the_database(client):
    response = add_item(client, sku_id="\u4ed3\u5e93-1")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["details"]["fields"][0]["field"] == "sku_id"


def test_invalid_quantity_shapes_are_rejected(client):
    payloads = [
        {"sku_id": "EMB-38", "quantity": 0},
        {"sku_id": "EMB-38", "quantity": -3},
        {"sku_id": "EMB-38", "quantity": 1.5},
        {"sku_id": "EMB-38", "quantity": "two"},
        {"sku_id": "EMB-38"},
        {"quantity": 1},
    ]
    for index, payload in enumerate(payloads):
        response = client.post(
            "/api/cart/items", json=payload, headers={"Idempotency-Key": f"invalid-{index}"}
        )
        assert response.status_code == 422, payload
        assert response.json()["error"]["code"] == "validation_error"


def test_quantity_above_stock_is_rejected_with_details_and_no_side_effects(client):
    before = stock_of(client, "MOS-42")  # seeded with 2
    response = add_item(client, sku_id="MOS-42", quantity=before + 1, key="over")

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "insufficient_stock"
    assert error["details"] == {"sku_id": "MOS-42", "requested": before + 1, "available": before}
    assert stock_of(client, "MOS-42") == before
    assert client.get("/api/cart").json()["total_items"] == 0


def test_out_of_stock_sku_is_rejected(client):
    response = add_item(client, sku_id="EMB-40", key="oos")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "insufficient_stock"
    assert response.json()["error"]["details"]["available"] == 0
    assert "out of stock" in response.json()["error"]["message"]


def test_client_supplied_price_and_stock_cannot_influence_the_server(client):
    unit_price = price_of(client, "EMB-38")
    response = client.post(
        "/api/cart/items",
        json={
            "sku_id": "EMB-38",
            "quantity": 1,
            "price_cents": 1,
            "available_qty": 9_999,
            "line_total_cents": 1,
        },
        headers={"Idempotency-Key": "spoof"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["item"]["unit_price_cents"] == unit_price
    assert body["item"]["line_total_cents"] == unit_price
    assert stock_of(client, "EMB-38") == 4 - 1


def test_totals_add_up_across_skus(client):
    add_item(client, sku_id="EMB-38", quantity=1, key="s1")
    add_item(client, sku_id="SAND-44", quantity=2, key="s2")
    cart = client.get("/api/cart").json()
    expected = price_of(client, "EMB-38") + 2 * price_of(client, "SAND-44")
    assert cart["total_items"] == 3
    assert cart["subtotal_cents"] == expected
    assert [item["sku_id"] for item in cart["items"]] == ["EMB-38", "SAND-44"]


def test_unexpected_failure_returns_a_structured_500(db_path, monkeypatch):
    """The error envelope must survive an unexpected exception."""
    from fastapi.testclient import TestClient

    import app.main as main_module

    def boom(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        raise RuntimeError("database on fire")

    monkeypatch.setattr(main_module.store, "add_cart_item", boom)
    raw_client = TestClient(main_module.create_app(db_path), raise_server_exceptions=False)
    response = raw_client.post(
        "/api/cart/items",
        json={"sku_id": "EMB-38", "quantity": 1},
        headers={"Idempotency-Key": "boom"},
    )
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert body["error"]["request_id"]
    assert "database on fire" not in response.text
