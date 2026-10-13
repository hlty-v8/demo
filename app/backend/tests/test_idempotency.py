"""Idempotency tests for POST /api/cart/items.

The guarantee: replaying a request with the same ``Idempotency-Key`` and the same
body must return the *identical* stored response and must not add the item twice.
The same key with a different body must be refused rather than silently merged.
"""

from __future__ import annotations

from app.catalog import PRODUCT

PRODUCT_URL = f"/api/products/{PRODUCT['id']}"


def stock_of(client, sku_id: str) -> int:
    skus = client.get(PRODUCT_URL).json()["skus"]
    return next(sku["available_qty"] for sku in skus if sku["id"] == sku_id)


def post(client, sku_id: str, quantity: int, key: str):
    return client.post(
        "/api/cart/items",
        json={"sku_id": sku_id, "quantity": quantity},
        headers={"Idempotency-Key": key},
    )


def test_replay_returns_the_identical_response_and_adds_once(client):
    first = post(client, "EMB-38", 2, "retry-me")
    second = post(client, "EMB-38", 2, "retry-me")

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json() == second.json()

    cart = client.get("/api/cart").json()
    assert cart["total_items"] == 2
    assert stock_of(client, "EMB-38") == 4 - 2


def test_replay_is_safe_many_times(client):
    for _ in range(5):
        response = post(client, "MOS-40", 3, "idem-loop")
        assert response.status_code == 201
    assert client.get("/api/cart").json()["total_items"] == 3
    assert stock_of(client, "MOS-40") == 7 - 3


def test_same_key_with_a_different_quantity_is_refused(client):
    assert post(client, "EMB-38", 1, "same-key").status_code == 201
    conflict = post(client, "EMB-38", 2, "same-key")

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_key_reuse"
    assert client.get("/api/cart").json()["total_items"] == 1


def test_same_key_with_a_different_sku_is_refused(client):
    assert post(client, "EMB-38", 1, "shared").status_code == 201
    conflict = post(client, "EMB-42", 1, "shared")

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_key_reuse"
    cart = client.get("/api/cart").json()
    assert [item["sku_id"] for item in cart["items"]] == ["EMB-38"]


def test_distinct_keys_add_twice(client):
    assert post(client, "EMB-38", 1, "key-a").status_code == 201
    assert post(client, "EMB-38", 1, "key-b").status_code == 201
    assert client.get("/api/cart").json()["total_items"] == 2


def test_replay_still_succeeds_after_the_stock_is_gone(client):
    """A replay must not be re-validated against stock: the work is already done."""
    assert post(client, "SAND-44", 3, "drain").status_code == 201
    assert stock_of(client, "SAND-44") == 0

    replay = post(client, "SAND-44", 3, "drain")
    assert replay.status_code == 201
    assert client.get("/api/cart").json()["total_items"] == 3

    # A *new* request for the same SKU is now a real failure.
    fresh = post(client, "SAND-44", 1, "new-key")
    assert fresh.status_code == 409
    assert fresh.json()["error"]["code"] == "insufficient_stock"


def test_rejected_requests_do_not_consume_the_idempotency_key(client):
    """A rejected add stores nothing, so the key can be retried successfully."""
    rejected = post(client, "MOS-42", 99, "retry-after-failure")
    assert rejected.status_code == 409

    retried = post(client, "MOS-42", 2, "retry-after-failure")
    assert retried.status_code == 201
    assert client.get("/api/cart").json()["total_items"] == 2


def test_idempotency_keys_are_scoped_per_key_not_per_cart_item(client):
    """The key identifies the request, so it cannot be replayed for another cart."""
    assert post(client, "EMB-38", 1, "key-cart-one").status_code == 201
    # A second cart (fresh cookie jar) reusing that key replays the *first*
    # response and therefore does not add to the new cart.
    client.cookies.clear()
    replayed = post(client, "EMB-38", 1, "key-cart-one")

    assert replayed.status_code == 201
    cart = client.get("/api/cart").json()
    assert cart["items"] == []
    assert cart["total_items"] == 0
