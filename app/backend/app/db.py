"""SQLite schema, connection handling and seeding.

Design notes:

* One connection per request, ``isolation_level=None`` (autocommit) so that the
  write paths can issue an explicit ``BEGIN IMMEDIATE``. That takes SQLite's
  write lock up front, which is what makes "check stock then decrement" and
  "check idempotency key then insert" atomic with respect to other writers -
  the alternative (deferred transactions) can fail late with SQLITE_BUSY after
  doing work.
* WAL journal mode plus a busy timeout lets readers (GET /api/cart) proceed
  while a writer holds the lock, instead of returning "database is locked".
* Stock is decremented when an item is added to the cart, i.e. a cart item is a
  reservation. That is the simplest model that still cannot oversell; expiry and
  checkout are explicitly out of scope for this exercise.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from . import catalog

# app/backend/ - the default database location, independent of the CWD.
BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = BACKEND_ROOT / "var" / "pdp.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL,
    currency    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS option_dimensions (
    product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    id         TEXT NOT NULL,
    name       TEXT NOT NULL,
    position   INTEGER NOT NULL,
    PRIMARY KEY (product_id, id)
);

CREATE TABLE IF NOT EXISTS option_values (
    product_id   TEXT NOT NULL,
    dimension_id TEXT NOT NULL,
    id           TEXT NOT NULL,
    label        TEXT NOT NULL,
    position     INTEGER NOT NULL,
    PRIMARY KEY (product_id, dimension_id, id),
    FOREIGN KEY (product_id, dimension_id)
        REFERENCES option_dimensions(product_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS skus (
    id            TEXT PRIMARY KEY,
    product_id    TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    price_cents   INTEGER NOT NULL CHECK (price_cents >= 0),
    available_qty INTEGER NOT NULL CHECK (available_qty >= 0)
);

CREATE TABLE IF NOT EXISTS sku_options (
    sku_id       TEXT NOT NULL REFERENCES skus(id) ON DELETE CASCADE,
    dimension_id TEXT NOT NULL,
    value_id     TEXT NOT NULL,
    PRIMARY KEY (sku_id, dimension_id)
);

CREATE TABLE IF NOT EXISTS carts (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cart_items (
    cart_id  TEXT NOT NULL REFERENCES carts(id) ON DELETE CASCADE,
    sku_id   TEXT NOT NULL REFERENCES skus(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (cart_id, sku_id)
);

-- One row per accepted write, keyed by the client's Idempotency-Key. The stored
-- response is what a replay returns, so a retried request is indistinguishable
-- from the original.
CREATE TABLE IF NOT EXISTS idempotency_keys (
    key           TEXT PRIMARY KEY,
    fingerprint   TEXT NOT NULL,
    status_code   INTEGER NOT NULL,
    response_json TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skus_product ON skus(product_id);
CREATE INDEX IF NOT EXISTS idx_cart_items_cart ON cart_items(cart_id);
"""


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    if db_path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path: str, seed: bool = True) -> None:
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        if seed:
            _seed(conn)
    finally:
        conn.close()


def _seed(conn: sqlite3.Connection) -> None:
    """Insert the seed catalogue. Idempotent: safe to run on every startup."""
    existing = conn.execute("SELECT COUNT(*) AS n FROM products").fetchone()["n"]
    if existing:
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT INTO products (id, name, description, currency) VALUES (?, ?, ?, ?)",
            (
                catalog.PRODUCT["id"],
                catalog.PRODUCT["name"],
                catalog.PRODUCT["description"],
                catalog.PRODUCT["currency"],
            ),
        )
        for position, dimension in enumerate(catalog.OPTION_DIMENSIONS):
            conn.execute(
                "INSERT INTO option_dimensions (product_id, id, name, position)"
                " VALUES (?, ?, ?, ?)",
                (catalog.PRODUCT["id"], dimension["id"], dimension["name"], position),
            )
            for value_position, value in enumerate(dimension["values"]):
                conn.execute(
                    "INSERT INTO option_values (product_id, dimension_id, id, label, position)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (
                        catalog.PRODUCT["id"],
                        dimension["id"],
                        value["id"],
                        value["label"],
                        value_position,
                    ),
                )
        for sku in catalog.SKUS:
            conn.execute(
                "INSERT INTO skus (id, product_id, price_cents, available_qty) VALUES (?, ?, ?, ?)",
                (sku["id"], catalog.PRODUCT["id"], sku["price_cents"], sku["available_qty"]),
            )
            for dimension_id in ("color", "size"):
                conn.execute(
                    "INSERT INTO sku_options (sku_id, dimension_id, value_id) VALUES (?, ?, ?)",
                    (sku["id"], dimension_id, sku[dimension_id]),
                )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def reset_stock(conn: sqlite3.Connection, sku_id: str, quantity: int) -> None:
    """Test helper: put a SKU back to a known stock level."""
    conn.execute("UPDATE skus SET available_qty = ? WHERE id = ?", (quantity, sku_id))


def db_path_from_env(default: Optional[str] = None) -> str:
    """Where the SQLite file lives.

    Resolved against the backend root rather than the process working directory,
    so ``uvicorn app.main:app`` and ``pytest`` agree no matter where they are
    started from. ``PDP_DB_PATH`` overrides it (tests use a temp file).
    """
    import os

    if default:
        return os.environ.get("PDP_DB_PATH", default)
    return os.environ.get("PDP_DB_PATH", str(DEFAULT_DB_PATH))
