"""Catalogue endpoint tests: shape, the seeded edge cases, errors, imaging."""

from __future__ import annotations

from app.catalog import PRODUCT, SKUS

PRODUCT_URL = f"/api/products/{PRODUCT['id']}"


def test_product_returns_two_option_dimensions_and_the_seed_skus(client):
    response = client.get(PRODUCT_URL)
    assert response.status_code == 200
    body = response.json()

    assert body["id"] == PRODUCT["id"]
    assert body["name"] == PRODUCT["name"]
    assert body["currency"] == "USD"
    assert [dimension["id"] for dimension in body["options"]] == ["color", "size"]
    for dimension in body["options"]:
        assert dimension["values"], f"{dimension['id']} should offer values"
        assert all(value["id"] and value["label"] for value in dimension["values"])
    assert len(body["skus"]) == len(SKUS) >= 6


def test_every_sku_carries_price_stock_image_and_option_values(client):
    skus = client.get(PRODUCT_URL).json()["skus"]
    for sku in skus:
        assert sku["id"]
        assert set(sku["options"]) == {"color", "size"}
        assert sku["price_cents"] > 0
        assert sku["available_qty"] >= 0
        assert sku["in_stock"] is (sku["available_qty"] > 0)
        assert sku["image_url"] == f"/api/skus/{sku['id']}/image"


def test_option_combinations_are_unique_and_one_is_deliberately_missing(client):
    skus = client.get(PRODUCT_URL).json()["skus"]
    combos = [(sku["options"]["color"], sku["options"]["size"]) for sku in skus]
    assert len(combos) == len(set(combos)), "two SKUs must not share a combination"
    # The seeded impossible combination the frontend has to disable.
    assert ("sand", "38") not in combos


def test_out_of_stock_sku_is_seeded_and_flagged(client):
    skus = {sku["id"]: sku for sku in client.get(PRODUCT_URL).json()["skus"]}
    assert skus["EMB-40"]["available_qty"] == 0
    assert skus["EMB-40"]["in_stock"] is False
    assert any(sku["in_stock"] for sku in skus.values())


def test_unknown_product_is_a_structured_404(client):
    response = client.get("/api/products/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "product_not_found"
    assert body["error"]["details"]["product_id"] == "does-not-exist"
    assert body["error"]["request_id"]
    assert response.headers["X-Request-Id"] == body["error"]["request_id"]


def test_sku_image_is_served_as_svg(client):
    response = client.get("/api/skus/EMB-38/image")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "EU" not in response.text  # the label is the raw size id
    assert ">38<" in response.text


def test_unknown_sku_image_is_a_structured_404(client):
    response = client.get("/api/skus/NOPE-99/image")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "sku_not_found"


def test_client_supplied_request_id_is_echoed(client):
    response = client.get(PRODUCT_URL, headers={"X-Request-Id": "trace-abc-123"})
    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == "trace-abc-123"
