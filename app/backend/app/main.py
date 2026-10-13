"""FastAPI application for the variant PDP.

Endpoints (full contract with examples in app/README.md and the repository
README.md):

    GET  /api/health                     liveness
    GET  /api/products/{product_id}      product + option dimensions + SKUs
    GET  /api/skus/{sku_id}/image        deterministic SVG placeholder image
    GET  /api/cart                       current cart (cookie-scoped)
    POST /api/cart/items                 add a SKU to the cart (Idempotency-Key)

Cross-cutting decisions:

* A cart is identified by the ``pdp_cart_id`` cookie, issued by the server. No
  authentication is in scope, so the cookie *is* the cart identity; it is
  HttpOnly and SameSite=Lax (the SPA is served same-origin through the Vite
  proxy in development).
* Every response carries ``X-Request-Id``, taken from the client when supplied
  and generated otherwise, and every error body repeats it, so a user-visible
  failure can be traced to a log line.
* Unexpected exceptions are converted to a structured 500 instead of leaking a
  traceback; the traceback is logged server-side only.
"""

from __future__ import annotations

import logging
import re
import uuid
from contextlib import closing
from typing import Any, Dict, Optional

from fastapi import FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import catalog
from . import db as db_module
from . import store
from .errors import (
    ApiError,
    missing_idempotency_key,
    product_not_found,
    sku_not_found,
    validation_error,
)
from .models import (
    AddCartItemRequest,
    AddCartItemResponse,
    CartOut,
    ErrorResponse,
    ProductOut,
)

logger = logging.getLogger("pdp.api")

CART_COOKIE = "pdp_cart_id"
CART_COOKIE_MAX_AGE = 60 * 60 * 24 * 30
MAX_IDEMPOTENCY_KEY_LENGTH = 255
_CART_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _request_id(request: Request) -> str:
    existing = getattr(request.state, "request_id", None)
    if existing:
        return existing
    supplied = request.headers.get("X-Request-Id", "")
    request_id = supplied.strip()[:128] if supplied.strip() else uuid.uuid4().hex
    request.state.request_id = request_id
    return request_id


def _error_response(status_code: int, payload: Dict[str, Any], request_id: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=payload, headers={"X-Request-Id": request_id})


def create_app(db_path: Optional[str] = None) -> FastAPI:
    """Build the app. ``db_path`` defaults to ``$PDP_DB_PATH`` or ./pdp.sqlite3."""
    path = db_path or db_module.db_path_from_env()
    db_module.init_db(path)

    app = FastAPI(
        title="Variant PDP API",
        version="1.0.0",
        description=(
            "Product detail page backend: catalogue reads plus a concurrency-safe, "
            "idempotent cart. See README.md for the documented contract."
        ),
        responses={500: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    )
    app.state.db_path = path

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = _request_id(request)
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _error_response(exc.status_code, exc.to_payload(_request_id(request)), _request_id(request))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields = [
            {
                "field": ".".join(str(part) for part in error["loc"][1:]) or "body",
                "message": error["msg"],
                "type": error["type"],
            }
            for error in exc.errors()
        ]
        return _error_response(
            422,
            {
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed.",
                    "details": {"fields": fields},
                    "request_id": _request_id(request),
                }
            },
            _request_id(request),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        request_id = _request_id(request)
        # Logged with the correlation id; the client only ever sees the envelope.
        logger.exception("unhandled error request_id=%s path=%s", request_id, request.url.path)
        return _error_response(
            500,
            {
                "error": {
                    "code": "internal_error",
                    "message": "Unexpected server error. Please retry.",
                    "details": {},
                    "request_id": request_id,
                }
            },
            request_id,
        )

    def _resolve_cart(request: Request) -> tuple[str, bool]:
        cookie = request.cookies.get(CART_COOKIE, "")
        if _CART_ID_RE.match(cookie):
            return cookie, False
        return uuid.uuid4().hex, True

    def _attach_cart_cookie(response: Response, cart_id: str, created: bool) -> None:
        if created:
            response.set_cookie(
                CART_COOKIE,
                cart_id,
                max_age=CART_COOKIE_MAX_AGE,
                httponly=True,
                samesite="lax",
                path="/",
            )

    @app.get("/api/health")
    def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/products/{product_id}", response_model=ProductOut)
    def get_product(product_id: str) -> Dict[str, Any]:
        with closing(db_module.connect(path)) as conn:
            product = store.fetch_product(conn, product_id)
        if product is None:
            raise product_not_found(product_id)
        return product

    @app.get("/api/skus/{sku_id}/image")
    def get_sku_image(sku_id: str) -> Response:
        try:
            svg = catalog.sku_image_svg(sku_id)
        except KeyError:
            raise sku_not_found(sku_id) from None
        return Response(
            content=svg,
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @app.get("/api/cart", response_model=CartOut)
    def get_cart(request: Request, response: Response) -> Dict[str, Any]:
        cart_id, created = _resolve_cart(request)
        with closing(db_module.connect(path)) as conn:
            store.ensure_cart(conn, cart_id)
            cart = store.fetch_cart(conn, cart_id)
        _attach_cart_cookie(response, cart_id, created)
        return cart

    @app.post("/api/cart/items", response_model=AddCartItemResponse, status_code=201)
    def add_cart_item(
        payload: AddCartItemRequest,
        request: Request,
        response: Response,
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        key = (idempotency_key or "").strip()
        if not key:
            raise missing_idempotency_key()
        if len(key) > MAX_IDEMPOTENCY_KEY_LENGTH:
            raise validation_error(
                f"Idempotency-Key must be at most {MAX_IDEMPOTENCY_KEY_LENGTH} characters.",
                {"field": "Idempotency-Key"},
            )

        cart_id, created = _resolve_cart(request)
        with closing(db_module.connect(path)) as conn:
            status_code, body, _replay = store.add_cart_item(
                conn,
                cart_id=cart_id,
                sku_id=payload.sku_id,
                quantity=payload.quantity,
                idempotency_key=key,
            )

        _attach_cart_cookie(response, cart_id, created)
        # A replay returns the stored status code and body verbatim, so a retry
        # is indistinguishable from the original response.
        response.status_code = status_code
        return body

    return app


app = create_app()
