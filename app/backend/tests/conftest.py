"""Shared fixtures for the backend test suite.

Each test gets its own SQLite file under pytest's temporary directory, so tests
are independent and can run in any order.

Environment guard: Starlette's ``TestClient`` (and uvicorn) need an asyncio event
loop, and on Windows asyncio builds its self-pipe from ``socket.socketpair()``,
which is a *loopback TCP* connection. In locked-down environments where loopback
connections are dropped, ``socketpair()`` blocks forever and any HTTP test would
hang the whole run. We probe for that once, with a timeout, and skip the
HTTP-level modules with an explicit reason instead of hanging. The SQLite-level
guarantees (stock, idempotency, concurrency) are covered by ``test_store.py``,
which needs no event loop, so the important logic is still tested there.
"""

from __future__ import annotations

import socket
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app import db as db_module  # noqa: E402
from app.main import CART_COOKIE, create_app  # noqa: E402

HTTP_TEST_MODULES = {
    "test_catalog.py",
    "test_cart.py",
    "test_idempotency.py",
    "test_concurrency.py",
    "test_live_server.py",
}


def _event_loop_available(timeout: float = 2.0) -> bool:
    """True when asyncio can build an event loop on this machine."""
    if sys.platform != "win32":
        return True

    outcome: dict[str, bool] = {}

    def attempt() -> None:
        try:
            left, right = socket.socketpair()
            left.close()
            right.close()
            outcome["ok"] = True
        except Exception:  # noqa: BLE001 - any failure means "not usable"
            outcome["ok"] = False

    worker = threading.Thread(target=attempt, daemon=True)
    worker.start()
    worker.join(timeout)
    return bool(outcome.get("ok"))


EVENT_LOOP_AVAILABLE = _event_loop_available()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    del config
    if EVENT_LOOP_AVAILABLE:
        return
    skip = pytest.mark.skip(
        reason=(
            "asyncio is unavailable here: socket.socketpair() (a loopback "
            "connection on Windows) does not complete, so an ASGI test client "
            "cannot run. Run this module on a machine with working loopback."
        )
    )
    for item in items:
        if Path(str(item.fspath)).name in HTTP_TEST_MODULES:
            item.add_marker(skip)


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    """A seeded database file.

    It is initialised here rather than only inside ``create_app``: helpers such
    as ``test_concurrency.set_stock`` write to the database *before* the first
    app is built, and an uninitialised path would make sqlite3 create an empty
    file ("no such table: skus"). ``init_db`` is idempotent, so the later
    ``create_app`` calls are unaffected.
    """
    path = str(tmp_path / "pdp-test.sqlite3")
    db_module.init_db(path)
    return path


@pytest.fixture()
def connection(db_path: str) -> Iterator[sqlite3.Connection]:
    """An open connection to the seeded database (no event loop needed)."""
    conn = db_module.connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def client(db_path: str) -> Iterator[TestClient]:
    with TestClient(create_app(db_path)) as test_client:
        yield test_client


@pytest.fixture()
def app_factory(db_path: str) -> Callable[[], TestClient]:
    """Build additional clients (for concurrency tests) against the same DB."""

    def factory() -> TestClient:
        return TestClient(create_app(db_path))

    return factory


def cart_cookie(client: TestClient) -> str:
    """Read the cart id the server issued to this client."""
    value = client.cookies.get(CART_COOKIE)
    assert value, "the server should have issued a cart cookie"
    return value
