"""End-to-end test against a real uvicorn server.

Everything else in this directory drives the app in-process (Starlette's
``TestClient``) or calls endpoint functions directly. This module is the only
one that starts an actual server, talks to it over real TCP with a real HTTP
client, and therefore exercises the parts the in-process tests cannot: uvicorn's
own request handling, HTTP status lines and headers, and - importantly - the
cart cookie travelling in a real ``Set-Cookie`` / ``Cookie`` round trip.

It needs an asyncio event loop (uvicorn) and a working loopback connection.

Two ways to satisfy that, in order:

1. ``PDP_LIVE_BASE_URL`` - use a server someone else already started (a shell, a
   container, a deployment). This is the escape hatch for hosts where a process
   spawned by Python cannot accept local connections.
2. Otherwise start ``uvicorn`` ourselves and wait for ``/api/health``.

If the self-started server never becomes reachable the module is skipped with
that explanation - except under CI, where it fails: a silent skip in CI would
hide exactly the regressions this test exists to catch.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

import httpx
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
STARTUP_TIMEOUT = 15.0
BASE_URL_ENV = "PDP_LIVE_BASE_URL"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _unreachable(reason: str) -> None:
    """Skip locally, but never silently skip in CI."""
    if os.environ.get("CI"):
        raise AssertionError(f"{reason} (refusing to skip in CI)")
    pytest.skip(
        f"{reason} Start the API yourself and re-run with "
        f"{BASE_URL_ENV}=http://127.0.0.1:8000 to exercise this module."
    )


@pytest.fixture(scope="module")
def live_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Yield the base URL of a real, running server."""
    provided = os.environ.get(BASE_URL_ENV)
    if provided:
        try:
            if httpx.get(f"{provided.rstrip('/')}/api/health", timeout=5).status_code != 200:
                _unreachable(f"{BASE_URL_ENV}={provided} did not answer /api/health.")
            else:
                yield provided.rstrip("/")
                return
        except httpx.HTTPError as error:
            _unreachable(f"{BASE_URL_ENV}={provided} is not reachable ({error!r}).")
            return

    workdir = tmp_path_factory.mktemp("live-server")
    db_path = workdir / "live.sqlite3"
    log_path = workdir / "uvicorn.log"
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    environment = {**os.environ, "PDP_DB_PATH": str(db_path), "PYTHONUNBUFFERED": "1"}

    # The child writes to a file rather than a pipe: some sandboxes refuse piped
    # stdio, and a file also keeps the server log readable after a failure.
    with log_path.open("wb") as log_file:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--app-dir",
                str(BACKEND_ROOT),
                # info rather than warning: the log then shows "Uvicorn running
                # on ...", which distinguishes "never started" from "started but
                # unreachable" when the readiness probe fails.
                "--log-level",
                "info",
            ],
            env=environment,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )

        try:
            deadline = time.monotonic() + STARTUP_TIMEOUT
            while True:
                if process.poll() is not None:
                    _unreachable(
                        "uvicorn exited before becoming ready:\n"
                        + log_path.read_text(encoding="utf-8", errors="replace")
                    )
                    return
                try:
                    if httpx.get(f"{base_url}/api/health", timeout=1.0).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() > deadline:
                    _unreachable(
                        f"the server did not become ready within {STARTUP_TIMEOUT:.0f}s "
                        f"(log: {log_path.read_text(encoding='utf-8', errors='replace')!r})"
                    )
                    return
                time.sleep(0.2)

            yield base_url
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                process.kill()
                process.wait(timeout=10)


def test_health_over_real_http(live_server: str) -> None:
    response = httpx.get(f"{live_server}/api/health", timeout=5)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-Id"]


def test_catalogue_over_real_http(live_server: str) -> None:
    response = httpx.get(f"{live_server}/api/products/aurora-trail-runner", timeout=5)
    assert response.status_code == 200
    body = response.json()
    assert [dimension["id"] for dimension in body["options"]] == ["color", "size"]
    assert len(body["skus"]) == 7


def test_unknown_product_is_a_structured_404_over_real_http(live_server: str) -> None:
    response = httpx.get(f"{live_server}/api/products/nope", timeout=5)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "product_not_found"


def test_cart_cookie_round_trip_and_idempotent_replay_over_real_http(live_server: str) -> None:
    """The whole point: a real server, a real client, a real cookie jar."""
    with httpx.Client(base_url=live_server, timeout=10) as client:
        empty = client.get("/api/cart")
        assert empty.status_code == 200
        assert empty.json()["total_items"] == 0
        assert client.cookies.get("pdp_cart_id"), "the server must issue a cart cookie"

        first = client.post(
            "/api/cart/items",
            json={"sku_id": "EMB-38", "quantity": 2},
            headers={"Idempotency-Key": "live-e2e-1"},
        )
        assert first.status_code == 201, first.text
        body = first.json()
        assert body["item"]["quantity"] == 2
        assert body["cart"]["total_items"] == 2

        # Same key, same body: identical response, no second add.
        replay = client.post(
            "/api/cart/items",
            json={"sku_id": "EMB-38", "quantity": 2},
            headers={"Idempotency-Key": "live-e2e-1"},
        )
        assert replay.status_code == 201
        assert replay.json() == body

        cart = client.get("/api/cart").json()
        assert cart["total_items"] == 2, "the replay must not have added again"
        assert cart["items"][0]["sku_id"] == "EMB-38"

        # The cart cookie survived the round trip, so this is the same cart.
        assert cart["id"] == body["cart_id"]

        product = client.get("/api/products/aurora-trail-runner").json()
        remaining = next(sku["available_qty"] for sku in product["skus"] if sku["id"] == "EMB-38")
        assert remaining == 4 - 2, "stock must have been reserved exactly once"


def test_insufficient_stock_over_real_http(live_server: str) -> None:
    with httpx.Client(base_url=live_server, timeout=10) as client:
        response = client.post(
            "/api/cart/items",
            json={"sku_id": "MOS-42", "quantity": 99},
            headers={"Idempotency-Key": "live-e2e-stock"},
        )
        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "insufficient_stock"
        assert error["details"]["available"] == 2
        assert error["request_id"]


def test_missing_idempotency_key_over_real_http(live_server: str) -> None:
    response = httpx.post(
        f"{live_server}/api/cart/items", json={"sku_id": "EMB-38", "quantity": 1}, timeout=5
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "idempotency_key_required"


def test_sku_image_over_real_http(live_server: str) -> None:
    response = httpx.get(f"{live_server}/api/skus/EMB-38/image", timeout=5)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
