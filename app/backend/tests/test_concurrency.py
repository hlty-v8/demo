"""Concurrency tests: the last unit must never be sold twice.

These drive the real HTTP stack with several threads at once. The endpoints are
sync functions, so FastAPI runs them in a worker thread pool and the requests
genuinely overlap; the SQLite write lock (``BEGIN IMMEDIATE``) is what
serialises them, and the conditional UPDATE is the second line of defence.

All racing clients are given the *same* cart cookie, so the cart assertions
describe one shared cart.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Tuple

from fastapi.testclient import TestClient

from app import db as db_module
from app.catalog import PRODUCT
from app.main import CART_COOKIE

PRODUCT_URL = f"/api/products/{PRODUCT['id']}"

RaceResult = Tuple[int, str, Dict[str, Any]]


def stock_of(client: TestClient, sku_id: str) -> int:
    skus = client.get(PRODUCT_URL).json()["skus"]
    return next(sku["available_qty"] for sku in skus if sku["id"] == sku_id)


def set_stock(db_path: str, sku_id: str, quantity: int) -> None:
    conn = db_module.connect(db_path)
    try:
        db_module.reset_stock(conn, sku_id, quantity)
    finally:
        conn.close()


def shared_cart_clients(app_factory: Callable[[], TestClient], count: int) -> List[TestClient]:
    """Several clients that all address the same cart (same cart cookie)."""
    first = app_factory()
    cart_id = first.get("/api/cart").cookies[CART_COOKIE]
    clients = [first]
    for _ in range(count - 1):
        extra = app_factory()
        extra.cookies.set(CART_COOKIE, cart_id)
        clients.append(extra)
    return clients


def race(clients: List[TestClient], sku_id: str, quantity: int, keys: List[str]) -> List[RaceResult]:
    """Fire one add per client as simultaneously as the barrier allows."""
    barrier = threading.Barrier(len(clients))

    def worker(index: int) -> RaceResult:
        barrier.wait(timeout=15)
        response = clients[index].post(
            "/api/cart/items",
            json={"sku_id": sku_id, "quantity": quantity},
            headers={"Idempotency-Key": keys[index]},
        )
        body = response.json()
        return response.status_code, body.get("error", {}).get("code", ""), body

    with ThreadPoolExecutor(max_workers=len(clients)) as pool:
        return list(pool.map(worker, range(len(clients))))


def test_two_requests_for_the_last_unit_produce_exactly_one_winner(app_factory, db_path):
    set_stock(db_path, "MOS-42", 1)
    clients = shared_cart_clients(app_factory, 2)

    results = race(clients, "MOS-42", 1, ["race-a", "race-b"])

    assert sorted(status for status, _, _ in results) == [201, 409], results
    assert [code for status, code, _ in results if status == 409] == ["insufficient_stock"]

    assert stock_of(clients[0], "MOS-42") == 0
    cart = clients[0].get("/api/cart").json()
    assert cart["total_items"] == 1
    assert cart["items"][0]["quantity"] == 1


def test_stock_never_goes_negative_under_many_concurrent_adds(app_factory, db_path):
    set_stock(db_path, "EMB-42", 3)
    attempts = 8
    clients = shared_cart_clients(app_factory, attempts)

    results = race(clients, "EMB-42", 1, [f"burst-{i}" for i in range(attempts)])

    assert len([status for status, _, _ in results if status == 201]) == 3, results
    assert [code for status, code, _ in results if status == 409] == ["insufficient_stock"] * 5

    assert stock_of(clients[0], "EMB-42") == 0
    assert clients[0].get("/api/cart").json()["total_items"] == 3


def test_concurrent_identical_keys_add_once_and_return_the_same_body(app_factory, db_path):
    set_stock(db_path, "SAND-42", 5)
    clients = shared_cart_clients(app_factory, 4)

    results = race(clients, "SAND-42", 2, ["same-key"] * 4)

    assert [status for status, _, _ in results] == [201] * 4, results
    first_body = results[0][2]
    assert all(body == first_body for _, _, body in results)
    assert first_body["cart"]["total_items"] == 2

    assert stock_of(clients[0], "SAND-42") == 5 - 2
    assert clients[0].get("/api/cart").json()["total_items"] == 2


def test_concurrent_quantity_larger_than_stock_fails_cleanly(app_factory, db_path):
    set_stock(db_path, "EMB-38", 2)
    clients = shared_cart_clients(app_factory, 3)

    results = race(clients, "EMB-38", 2, ["big-a", "big-b", "big-c"])

    assert sorted(status for status, _, _ in results) == [201, 409, 409]
    assert stock_of(clients[0], "EMB-38") == 0
    assert clients[0].get("/api/cart").json()["total_items"] == 2
