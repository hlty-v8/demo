"""Contract tests that need no event loop.

Starlette generates the OpenAPI document synchronously, so the published API
contract can be asserted anywhere - including environments where an ASGI client
cannot run. This is the documentation the README points reviewers at, so it is
worth testing rather than trusting.
"""

from __future__ import annotations

from app.main import create_app


def schema_for(db_path: str) -> dict:
    return create_app(db_path).openapi()


def test_the_three_documented_endpoints_exist_with_the_right_methods(db_path):
    paths = schema_for(db_path)["paths"]

    assert "get" in paths["/api/products/{product_id}"]
    assert "post" in paths["/api/cart/items"]
    assert "get" in paths["/api/cart"]


def test_cart_post_documents_201_and_the_idempotency_header(db_path):
    post = schema_for(db_path)["paths"]["/api/cart/items"]["post"]

    assert "201" in post["responses"]
    header_parameters = [
        parameter
        for parameter in post.get("parameters", [])
        if parameter.get("in") == "header"
    ]
    assert any(parameter["name"] == "Idempotency-Key" for parameter in header_parameters)


def test_cart_post_request_body_only_accepts_sku_and_quantity(db_path):
    schema = schema_for(db_path)
    body_ref = schema["paths"]["/api/cart/items"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    model_name = body_ref.rsplit("/", 1)[-1]
    properties = schema["components"]["schemas"][model_name]["properties"]

    assert set(properties) == {"sku_id", "quantity"}


def test_product_response_documents_price_stock_and_image(db_path):
    schema = schema_for(db_path)
    sku_properties = schema["components"]["schemas"]["SkuOut"]["properties"]

    assert {"id", "options", "price_cents", "available_qty", "in_stock", "image_url"} <= set(
        sku_properties
    )


def test_error_envelope_is_part_of_the_documented_contract(db_path):
    schemas = schema_for(db_path)["components"]["schemas"]

    assert {"ErrorBody", "ErrorResponse"} <= set(schemas)
    assert {"code", "message", "details", "request_id"} <= set(
        schemas["ErrorBody"]["properties"]
    )


def test_cart_response_documents_totals(db_path):
    schema = schema_for(db_path)
    cart_properties = schema["components"]["schemas"]["CartOut"]["properties"]

    assert {"id", "items", "total_items", "subtotal_cents"} <= set(cart_properties)
