"""The single-origin deployment path: FastAPI serving the built frontend.

`PDP_STATIC_DIR` makes the app host the compiled SPA itself, so the container
image is one process on one port: no proxy, no CORS, and the cart cookie is
first-party. These tests build a throwaway `dist/` and check that the SPA is
served while the API keeps working - and that API errors stay JSON, not HTML.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

INDEX_HTML = "<!doctype html><title>PDP</title><div id=root></div>"
ASSET_JS = "console.log('built asset');"


@pytest.fixture()
def static_site(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (dist / "assets" / "app.js").write_text(ASSET_JS, encoding="utf-8")
    return dist


@pytest.fixture()
def spa_client(db_path: str, static_site: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("PDP_STATIC_DIR", str(static_site))
    return TestClient(create_app(db_path))


def test_the_spa_is_served_at_the_root(spa_client: TestClient) -> None:
    response = spa_client.get("/")

    assert response.status_code == 200
    assert "id=root" in response.text
    assert response.headers["content-type"].startswith("text/html")


def test_built_assets_are_served(spa_client: TestClient) -> None:
    response = spa_client.get("/assets/app.js")

    assert response.status_code == 200
    assert "built asset" in response.text


def test_the_api_still_wins_over_the_spa_mount(spa_client: TestClient) -> None:
    health = spa_client.get("/api/health")
    product = spa_client.get("/api/products/aurora-trail-runner")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert product.status_code == 200
    assert len(product.json()["skus"]) == 7


def test_api_errors_stay_json_when_the_spa_is_mounted(spa_client: TestClient) -> None:
    response = spa_client.get("/api/products/nope")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "product_not_found"


def test_a_cart_write_works_from_the_same_origin(spa_client: TestClient) -> None:
    response = spa_client.post(
        "/api/cart/items",
        json={"sku_id": "EMB-38", "quantity": 1},
        headers={"Idempotency-Key": "spa-same-origin"},
    )

    assert response.status_code == 201
    assert response.cookies.get("pdp_cart_id")
    assert response.json()["cart"]["total_items"] == 1


def test_without_the_static_dir_nothing_is_mounted(db_path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PDP_STATIC_DIR", raising=False)
    client = TestClient(create_app(db_path))

    root = client.get("/")

    # Starlette's own 404 (the SPA is not served); the API is unaffected.
    assert root.status_code == 404
    assert "id=root" not in root.text
    assert client.get("/api/health").status_code == 200
