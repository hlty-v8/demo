"""Data access: catalogue reads and the cart write path.

The write path is the interesting part. Adding to the cart has to be atomic with
respect to three things at once: the stock decrement, the cart item upsert, and
the idempotency record. All three happen inside one ``BEGIN IMMEDIATE``
transaction, so either the add is fully recorded or nothing is.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from . import catalog
from .errors import (
    idempotency_key_reuse,
    insufficient_stock,
    sku_not_found,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fingerprint(sku_id: str, quantity: int) -> str:
    """Stable hash of the semantically meaningful request fields.

    Only fields that change the outcome take part, so the same intent always
    produces the same fingerprint and a changed intent never does.
    """
    payload = json.dumps({"sku_id": sku_id, "quantity": quantity}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Catalogue reads
# --------------------------------------------------------------------------


def _options_for_sku(conn: sqlite3.Connection, sku_id: str) -> Dict[str, str]:
    rows = conn.execute(
        "SELECT dimension_id, value_id FROM sku_options WHERE sku_id = ? ORDER BY dimension_id",
        (sku_id,),
    ).fetchall()
    return {row["dimension_id"]: row["value_id"] for row in rows}


def fetch_product(conn: sqlite3.Connection, product_id: str) -> Optional[Dict[str, Any]]:
    product = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if product is None:
        return None

    options = []
    for dimension in conn.execute(
        "SELECT id, name FROM option_dimensions WHERE product_id = ? ORDER BY position",
        (product_id,),
    ).fetchall():
        values = conn.execute(
            "SELECT id, label FROM option_values"
            " WHERE product_id = ? AND dimension_id = ? ORDER BY position",
            (product_id, dimension["id"]),
        ).fetchall()
        options.append(
            {
                "id": dimension["id"],
                "name": dimension["name"],
                "values": [{"id": v["id"], "label": v["label"]} for v in values],
            }
        )

    skus = []
    for sku in conn.execute(
        "SELECT id, price_cents, available_qty FROM skus WHERE product_id = ? ORDER BY id",
        (product_id,),
    ).fetchall():
        skus.append(
            {
                "id": sku["id"],
                "options": _options_for_sku(conn, sku["id"]),
                "price_cents": sku["price_cents"],
                "available_qty": sku["available_qty"],
                "in_stock": sku["available_qty"] > 0,
                "image_url": catalog.sku_image_url(sku["id"]),
            }
        )

    return {
        "id": product["id"],
        "name": product["name"],
        "description": product["description"],
        "currency": product["currency"],
        "options": options,
        "skus": skus,
    }


def fetch_sku(conn: sqlite3.Connection, sku_id: str) -> Optional[Dict[str, Any]]:
    sku = conn.execute(
        "SELECT id, price_cents, available_qty FROM skus WHERE id = ?", (sku_id,)
    ).fetchone()
    if sku is None:
        return None
    return {
        "id": sku["id"],
        "options": _options_for_sku(conn, sku_id),
        "price_cents": sku["price_cents"],
        "available_qty": sku["available_qty"],
        "in_stock": sku["available_qty"] > 0,
        "image_url": catalog.sku_image_url(sku_id),
    }


# --------------------------------------------------------------------------
# Cart
# --------------------------------------------------------------------------


def ensure_cart(conn: sqlite3.Connection, cart_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO carts (id, created_at) VALUES (?, ?)", (cart_id, _utc_now())
    )


def fetch_cart(conn: sqlite3.Connection, cart_id: str) -> Dict[str, Any]:
    rows = conn.execute(
        """
        SELECT ci.sku_id, ci.quantity, s.price_cents, s.available_qty
        FROM cart_items AS ci
        JOIN skus AS s ON s.id = ci.sku_id
        WHERE ci.cart_id = ?
        ORDER BY ci.sku_id
        """,
        (cart_id,),
    ).fetchall()

    items = []
    total_items = 0
    subtotal_cents = 0
    for row in rows:
        line_total = row["quantity"] * row["price_cents"]
        total_items += row["quantity"]
        subtotal_cents += line_total
        items.append(
            {
                "sku_id": row["sku_id"],
                "quantity": row["quantity"],
                "unit_price_cents": row["price_cents"],
                "line_total_cents": line_total,
                "available_qty": row["available_qty"],
                "image_url": catalog.sku_image_url(row["sku_id"]),
                "options": _options_for_sku(conn, row["sku_id"]),
            }
        )

    return {
        "id": cart_id,
        "items": items,
        "total_items": total_items,
        "subtotal_cents": subtotal_cents,
    }


def add_cart_item(
    conn: sqlite3.Connection,
    *,
    cart_id: str,
    sku_id: str,
    quantity: int,
    idempotency_key: str,
) -> Tuple[int, Dict[str, Any], bool]:
    """Add (or top up) a cart line.

    Returns ``(status_code, response_body, was_replay)``. Raises ApiError for a
    missing SKU, insufficient stock or an idempotency key reused with a
    different body.

    Concurrency: ``BEGIN IMMEDIATE`` takes the database write lock before the
    read of the current stock, so two simultaneous "buy the last unit" requests
    are serialised. The loser sees ``available_qty = 0`` and gets a structured
    409 instead of a negative stock row. The stock check and the decrement are
    also a single conditional UPDATE, so even without the transaction the row
    could not go negative.
    """
    request_fingerprint = fingerprint(sku_id, quantity)
    committed = False
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            "SELECT fingerprint, status_code, response_json FROM idempotency_keys WHERE key = ?",
            (idempotency_key,),
        ).fetchone()
        if existing is not None:
            conn.execute("COMMIT")
            committed = True
            if existing["fingerprint"] != request_fingerprint:
                raise idempotency_key_reuse(idempotency_key)
            # Replay: byte-identical response, no second add.
            return existing["status_code"], json.loads(existing["response_json"]), True

        ensure_cart(conn, cart_id)

        sku = conn.execute(
            "SELECT id, price_cents, available_qty FROM skus WHERE id = ?", (sku_id,)
        ).fetchone()
        if sku is None:
            raise sku_not_found(sku_id)

        updated = conn.execute(
            "UPDATE skus SET available_qty = available_qty - ?"
            " WHERE id = ? AND available_qty >= ?",
            (quantity, sku_id, quantity),
        )
        if updated.rowcount == 0:
            raise insufficient_stock(sku_id, quantity, sku["available_qty"])

        current = conn.execute(
            "SELECT quantity FROM cart_items WHERE cart_id = ? AND sku_id = ?",
            (cart_id, sku_id),
        ).fetchone()
        new_quantity = (current["quantity"] if current else 0) + quantity
        conn.execute(
            """
            INSERT INTO cart_items (cart_id, sku_id, quantity) VALUES (?, ?, ?)
            ON CONFLICT(cart_id, sku_id) DO UPDATE SET quantity = excluded.quantity
            """,
            (cart_id, sku_id, new_quantity),
        )

        kart = fetch_cart(conn, cart_id)
        item = next(entry for entry in kart["items"] if entry["sku_id"] == sku_id)
        body = {"cart_id": cart_id, "item": item, "cart": kart}

        conn.execute(
            "INSERT INTO idempotency_keys (key, fingerprint, status_code, response_json, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (idempotency_key, request_fingerprint, 201, json.dumps(body), _utc_now()),
        )
        conn.execute("COMMIT")
        committed = True
        return 201, body, False
    except Exception:
        if not committed:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:  # pragma: no cover - defensive
                pass
        raise
