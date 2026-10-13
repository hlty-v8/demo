"""SQLite-level tests for the cart write path.

These cover the guarantees the HTTP tests also assert, but without an ASGI
client: stock is decremented atomically, idempotency keys are honoured, rejected
requests leave no trace, and concurrent requests can never oversell. They run
anywhere, including environments where asyncio cannot build an event loop.
"""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import List, Tuple

import pytest

from app import db as db_module
from app import store
from app.errors import ApiError

CART = "cart-test-1"


def add(conn: sqlite3.Connection, sku_id: str = "EMB-38", quantity: int = 1, key: str = "k1"):
    return store.add_cart_item(
        conn, cart_id=CART, sku_id=sku_id, quantity=quantity, idempotency_key=key
    )


def stock_of(conn: sqlite3.Connection, sku_id: str) -> int:
    return conn.execute(
        "SELECT available_qty FROM skus WHERE id = ?", (sku_id,)
    ).fetchone()["available_qty"]


def cart_quantity(conn: sqlite3.Connection, sku_id: str) -> int:
    row = conn.execute(
        "SELECT quantity FROM cart_items WHERE cart_id = ? AND sku_id = ?", (CART, sku_id)
    ).fetchone()
    return row["quantity"] if row else 0


def idempotency_rows(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM idempotency_keys").fetchone()["n"]


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


def test_add_item_decrements_stock_and_returns_the_cart(connection):
    before = stock_of(connection, "EMB-38")
    price = connection.execute(
        "SELECT price_cents FROM skus WHERE id = 'EMB-38'"
    ).fetchone()["price_cents"]
    status, body, replay = add(connection, "EMB-38", 2, "key-happy")

    assert status == 201
    assert replay is False
    assert body["cart_id"] == CART
    assert body["item"]["sku_id"] == "EMB-38"
    assert body["item"]["quantity"] == 2
    assert body["item"]["unit_price_cents"] == price
    assert body["item"]["line_total_cents"] == price * 2
    assert body["cart"]["total_items"] == 2
    assert body["cart"]["subtotal_cents"] == price * 2
    assert stock_of(connection, "EMB-38") == before - 2


def test_prices_come_from_the_database_not_the_caller(connection):
    _, body, _ = add(connection, "SAND-44", 1, "key-price")
    expected = connection.execute(
        "SELECT price_cents FROM skus WHERE id = 'SAND-44'"
    ).fetchone()["price_cents"]
    assert body["item"]["unit_price_cents"] == expected


def test_adding_the_same_sku_twice_accumulates_one_line(connection):
    add(connection, "EMB-38", 1, "key-a")
    add(connection, "EMB-38", 2, "key-b")
    assert cart_quantity(connection, "EMB-38") == 3
    assert stock_of(connection, "EMB-38") == 4 - 3


def test_cart_totals_across_several_skus(connection):
    add(connection, "EMB-38", 1, "key-1")
    add(connection, "SAND-44", 2, "key-2")
    cart = store.fetch_cart(connection, CART)

    assert [item["sku_id"] for item in cart["items"]] == ["EMB-38", "SAND-44"]
    assert cart["total_items"] == 3
    assert cart["subtotal_cents"] == sum(
        connection.execute("SELECT price_cents FROM skus WHERE id = ?", (item["sku_id"],))
        .fetchone()["price_cents"]
        * item["quantity"]
        for item in cart["items"]
    )


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


def test_replay_returns_the_stored_response_and_adds_once(connection):
    first = add(connection, "EMB-38", 2, "key-replay")
    stock_after_first = stock_of(connection, "EMB-38")

    second = add(connection, "EMB-38", 2, "key-replay")

    assert second[0] == first[0] == 201
    assert second[1] == first[1]
    assert second[2] is True
    assert stock_of(connection, "EMB-38") == stock_after_first
    assert cart_quantity(connection, "EMB-38") == 2
    assert idempotency_rows(connection) == 1


def test_replay_survives_repeated_calls(connection):
    for _ in range(6):
        status, _, _ = add(connection, "MOS-40", 3, "key-loop")
        assert status == 201
    assert cart_quantity(connection, "MOS-40") == 3
    assert stock_of(connection, "MOS-40") == 7 - 3


def test_same_key_with_a_different_body_is_refused(connection):
    add(connection, "EMB-38", 1, "key-shared")

    with pytest.raises(ApiError) as caught:
        add(connection, "EMB-38", 2, "key-shared")

    assert caught.value.status_code == 409
    assert caught.value.code == "idempotency_key_reuse"
    assert cart_quantity(connection, "EMB-38") == 1
    assert stock_of(connection, "EMB-38") == 4 - 1


def test_same_key_with_a_different_sku_is_refused(connection):
    add(connection, "EMB-38", 1, "key-cross")
    with pytest.raises(ApiError) as caught:
        add(connection, "EMB-42", 1, "key-cross")
    assert caught.value.code == "idempotency_key_reuse"
    assert cart_quantity(connection, "EMB-42") == 0


def test_fingerprint_ignores_nothing_that_changes_the_outcome(connection):
    assert store.fingerprint("EMB-38", 1) != store.fingerprint("EMB-38", 2)
    assert store.fingerprint("EMB-38", 1) != store.fingerprint("EMB-42", 1)
    assert store.fingerprint("EMB-38", 1) == store.fingerprint("EMB-38", 1)


# --------------------------------------------------------------------------
# Rejections leave no trace
# --------------------------------------------------------------------------


def test_insufficient_stock_is_atomic(connection):
    with pytest.raises(ApiError) as caught:
        add(connection, "MOS-42", 99, "key-over")

    assert caught.value.status_code == 409
    assert caught.value.code == "insufficient_stock"
    assert caught.value.details == {"sku_id": "MOS-42", "requested": 99, "available": 2}
    assert stock_of(connection, "MOS-42") == 2
    assert cart_quantity(connection, "MOS-42") == 0
    assert idempotency_rows(connection) == 0


def test_out_of_stock_sku_is_rejected(connection):
    with pytest.raises(ApiError) as caught:
        add(connection, "EMB-40", 1, "key-oos")
    assert caught.value.details["available"] == 0
    assert stock_of(connection, "EMB-40") == 0


def test_unknown_sku_is_rejected(connection):
    with pytest.raises(ApiError) as caught:
        add(connection, "NOPE-99", 1, "key-missing")
    assert caught.value.status_code == 404
    assert caught.value.code == "sku_not_found"
    assert idempotency_rows(connection) == 0


def test_a_rejected_request_does_not_consume_its_key(connection):
    with pytest.raises(ApiError):
        add(connection, "MOS-42", 99, "key-retry")

    status, _, replay = add(connection, "MOS-42", 2, "key-retry")
    assert (status, replay) == (201, False)
    assert cart_quantity(connection, "MOS-42") == 2


def test_rollback_restores_state_when_a_later_step_fails(connection, monkeypatch):
    """If anything after the stock decrement explodes, nothing is committed."""
    original = store.fetch_cart

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("cart assembly failed")

    monkeypatch.setattr(store, "fetch_cart", boom)
    with pytest.raises(RuntimeError):
        add(connection, "EMB-38", 1, "key-boom")

    monkeypatch.setattr(store, "fetch_cart", original)
    assert stock_of(connection, "EMB-38") == 4
    assert cart_quantity(connection, "EMB-38") == 0
    assert idempotency_rows(connection) == 0


# --------------------------------------------------------------------------
# Concurrency (threads, no event loop needed)
# --------------------------------------------------------------------------


def _worker(args: Tuple[str, str, int, str]) -> Tuple[str, object]:
    db_path, sku_id, quantity, key = args
    conn = db_module.connect(db_path)
    try:
        try:
            status, body, replay = store.add_cart_item(
                conn, cart_id=CART, sku_id=sku_id, quantity=quantity, idempotency_key=key
            )
            return ("ok", (status, body, replay))
        except ApiError as error:
            return ("api_error", (error.status_code, error.code))
    finally:
        conn.close()


def run_concurrently(db_path: str, jobs: List[Tuple[str, str, int, str]]) -> List[Tuple[str, object]]:
    barrier = threading.Barrier(len(jobs))

    def start(job: Tuple[str, str, int, str]) -> Tuple[str, object]:
        barrier.wait(timeout=15)
        return _worker(job)

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        return list(pool.map(start, jobs))


def test_two_threads_racing_for_the_last_unit_produce_one_winner(connection, db_path):
    db_module.reset_stock(connection, "MOS-42", 1)
    jobs = [
        (db_path, "MOS-42", 1, "race-a"),
        (db_path, "MOS-42", 1, "race-b"),
    ]

    results = run_concurrently(db_path, jobs)

    outcomes = sorted(result[0] for result in results)
    assert outcomes == ["api_error", "ok"], results
    failures = [result[1] for result in results if result[0] == "api_error"]
    assert failures == [(409, "insufficient_stock")]
    assert stock_of(connection, "MOS-42") == 0
    assert cart_quantity(connection, "MOS-42") == 1


def test_stock_never_goes_negative_under_a_burst(connection, db_path):
    db_module.reset_stock(connection, "EMB-42", 3)
    jobs = [(db_path, "EMB-42", 1, f"burst-{index}") for index in range(8)]

    results = run_concurrently(db_path, jobs)

    successes = [result for result in results if result[0] == "ok"]
    failures = [result for result in results if result[0] == "api_error"]
    assert len(successes) == 3, results
    assert len(failures) == 5, results
    assert all(result[1] == (409, "insufficient_stock") for result in failures)
    assert stock_of(connection, "EMB-42") == 0
    assert cart_quantity(connection, "EMB-42") == 3


def test_identical_keys_in_parallel_add_once(connection, db_path):
    db_module.reset_stock(connection, "SAND-42", 5)
    jobs = [(db_path, "SAND-42", 2, "same-key") for _ in range(6)]

    results = run_concurrently(db_path, jobs)

    assert all(result[0] == "ok" for result in results), results
    bodies = [result[1][1] for result in results]  # type: ignore[index]
    assert all(body == bodies[0] for body in bodies)
    assert stock_of(connection, "SAND-42") == 3
    assert cart_quantity(connection, "SAND-42") == 2
    assert idempotency_rows(connection) == 1


# --------------------------------------------------------------------------
# Catalogue reads (also used by the HTTP layer)
# --------------------------------------------------------------------------


def test_fetch_product_shape_and_seeded_edge_cases(connection):
    product = store.fetch_product(connection, "aurora-trail-runner")
    assert product is not None
    assert [dimension["id"] for dimension in product["options"]] == ["color", "size"]
    assert len(product["skus"]) >= 6

    skus = {sku["id"]: sku for sku in product["skus"]}
    assert skus["EMB-40"]["available_qty"] == 0
    assert skus["EMB-40"]["in_stock"] is False
    assert skus["EMB-38"]["in_stock"] is True
    combinations = {(sku["options"]["color"], sku["options"]["size"]) for sku in product["skus"]}
    assert ("sand", "38") not in combinations
    assert len(combinations) == len(product["skus"])


def test_fetch_product_returns_none_for_an_unknown_id(connection):
    assert store.fetch_product(connection, "nope") is None
    assert store.fetch_sku(connection, "nope") is None


def test_fetch_cart_is_empty_for_a_new_cart(connection):
    cart = store.fetch_cart(connection, "brand-new-cart")
    assert cart == {"id": "brand-new-cart", "items": [], "total_items": 0, "subtotal_cents": 0}


def test_seeding_is_idempotent(connection, db_path):
    db_module.init_db(db_path)
    db_module.init_db(db_path)
    count = connection.execute("SELECT COUNT(*) AS n FROM skus").fetchone()["n"]
    assert count == 7
