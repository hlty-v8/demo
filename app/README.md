"""Contract and setup notes for the variant PDP app.

Sections: [quickstart](#quickstart) - [domain model](#domain-model) -
[API contract](#api-contract) - [backend decisions](#backend-decisions) -
[frontend decisions](#frontend-decisions) - [tests](#tests) -
[limitations](#limitations)
"""

# Variant PDP

A small but production-minded product detail page: a FastAPI backend that owns
price and stock, and a React/TypeScript frontend that renders variants without
ever guessing what the server knows.

```bash
# backend
cd app/backend
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m uvicorn app.main:app --reload --port 8000
python -m pytest tests -q

# frontend (second terminal)
cd app/frontend
pnpm install
pnpm dev          # http://localhost:5173  (proxies /api -> 127.0.0.1:8000)
pnpm test
pnpm build
```

`app/run.sh` / `app/run.ps1` start both processes with one command. The database
is a SQLite file created and seeded on first start (`app/backend/var/pdp.sqlite3`,
override with `PDP_DB_PATH`), so the shop is populated the moment the server
starts.

### Container

```bash
docker build -t variant-pdp .          # from the repository root
docker run --rm -p 8000:8000 -v pdp-data:/data variant-pdp
```

The image is one process on one port. Node builds the SPA in the first stage;
the Python stage receives `dist/` and serves it at `/` when `PDP_STATIC_DIR` is
set, so the API and the page share an origin - no proxy, no CORS, and the cart
cookie is first-party. The database lives on the `/data` volume.

## Domain model

One product (`aurora-trail-runner`) with two option dimensions, **Colour**
(Ember, Moss, Sand) and **Size** (EU 38, 40, 42, 44), and seven SKUs:

| SKU | Colour | Size | Price | Stock | Purpose |
| --- | --- | --- | --- | --- | --- |
| EMB-38 | Ember | 38 | $129.00 | 4 | |
| EMB-40 | Ember | 40 | $129.00 | **0** | the out-of-stock SKU |
| EMB-42 | Ember | 42 | $129.00 | 6 | |
| MOS-40 | Moss | 40 | $129.00 | 7 | |
| MOS-42 | Moss | 42 | $132.00 | 2 | price differs by size |
| SAND-42 | Sand | 42 | $135.00 | 5 | |
| SAND-44 | Sand | 44 | $135.00 | 3 | |

**Sand x 38 deliberately has no SKU**, which gives the UI a genuinely impossible
combination to disable rather than a synthetic one. Prices are integer cents
everywhere; the only place a currency string exists is the frontend's formatter.

## API contract

Every error, whatever its cause, uses one envelope:

```json
{
  "error": {
    "code": "insufficient_stock",
    "message": "Only 2 left of MOS-42.",
    "details": { "sku_id": "MOS-42", "requested": 5, "available": 2 },
    "request_id": "9f1c1c0e4b8a4c1f9a0d2e3b4c5d6e7f"
  }
}
```

`code` is stable and machine-readable; `message` is safe to display; `details`
carries field context; `request_id` also travels in the `X-Request-Id` response
header (and is echoed when the client supplies one), so a user-visible failure
can be traced to a log line.

### `GET /api/products/{product_id}` → 200

```json
{
  "id": "aurora-trail-runner",
  "name": "Aurora Trail Runner",
  "description": "A lightweight trail shoe ...",
  "currency": "USD",
  "options": [
    { "id": "color", "name": "Colour",
      "values": [ { "id": "ember", "label": "Ember" }, { "id": "moss", "label": "Moss" }, { "id": "sand", "label": "Sand" } ] },
    { "id": "size", "name": "Size",
      "values": [ { "id": "38", "label": "EU 38" }, { "id": "40", "label": "EU 40" }, { "id": "42", "label": "EU 42" }, { "id": "44", "label": "EU 44" } ] }
  ],
  "skus": [
    { "id": "EMB-42", "options": { "color": "ember", "size": "42" },
      "price_cents": 12900, "available_qty": 6, "in_stock": true,
      "image_url": "/api/skus/EMB-42/image" }
  ]
}
```

`404 product_not_found` for an unknown id.

### `GET /api/skus/{sku_id}/image` → 200 `image/svg+xml`

A deterministic placeholder drawn per SKU (colour plus size label), so the
repository needs no binary assets and the frontend always has a real image.

### `GET /api/cart` → 200

```json
{ "id": "…", "items": [ { "sku_id": "EMB-42", "quantity": 2, "unit_price_cents": 12900,
                          "line_total_cents": 25800, "available_qty": 4,
                          "image_url": "/api/skus/EMB-42/image",
                          "options": { "color": "ember", "size": "42" } } ],
  "total_items": 2, "subtotal_cents": 25800 }
```

The cart is identified by the `pdp_cart_id` cookie, which the server sets
(`HttpOnly`, `SameSite=Lax`) the first time it sees a client.

### `POST /api/cart/items` → 201

Headers: `Idempotency-Key: <string>` (**required**, ≤ 255 chars).
Body: `{ "sku_id": "EMB-42", "quantity": 1 }`.

```json
{ "cart_id": "…",
  "item": { "sku_id": "EMB-42", "quantity": 1, "unit_price_cents": 12900, "line_total_cents": 12900, "available_qty": 5, "image_url": "/api/skus/EMB-42/image", "options": { "color": "ember", "size": "42" } },
  "cart": { "id": "…", "items": [ … ], "total_items": 1, "subtotal_cents": 12900 } }
```

| Status | `code` | When |
| --- | --- | --- |
| 201 | - | added (or an exact replay of a previous add) |
| 400 | `idempotency_key_required` | header missing or blank |
| 404 | `sku_not_found` | unknown SKU id |
| 409 | `insufficient_stock` | not enough stock; `details` carries `requested` and `available` |
| 409 | `idempotency_key_reuse` | the key was already used with a different body |
| 422 | `validation_error` | body failed validation; `details.fields` lists the problems |
| 500 | `internal_error` | unexpected failure; logged with the request id, never leaked |

`price_cents`, `available_qty` or any other field supplied by the client is
ignored by construction (`extra="ignore"`), and the server always re-reads both
price and stock from SQLite.

## Backend decisions

**Never trust the client.** The request model accepts only `sku_id` and
`quantity`. Price comes from the `skus` row, and the quantity is checked against
the current stock, inside the transaction that decrements it.

**Preventing oversell.** Adding to the cart is a reservation: the quantity is
removed from stock immediately, so a cart cannot be built from units that do not
exist. The write path is one `BEGIN IMMEDIATE` transaction containing (1) the
idempotency lookup, (2) a conditional `UPDATE skus SET available_qty =
available_qty - ? WHERE id = ? AND available_qty >= ?`, and (3) the cart upsert
plus the idempotency record. `BEGIN IMMEDIATE` takes SQLite's write lock *before*
the stock is read, so two simultaneous "buy the last unit" requests are
serialised; the loser sees `available_qty` at its new value and gets a structured
409. The conditional `UPDATE` is a second line of defence: even without the
transaction the row could not go negative, and `rowcount == 0` distinguishes
"unknown SKU" from "not enough stock". Tests race 2 and 8 threads at the last
unit and assert exactly the right number of winners.

**Idempotency.** The key, a SHA-256 fingerprint of `(sku_id, quantity)`, the
status code and the full response body are stored in `idempotency_keys` in the
same transaction. A replay returns the stored status and body verbatim
(`was_replay=True` internally), so a client retry is indistinguishable from the
original and cannot double-add. The same key with a *different* fingerprint is a
409 `idempotency_key_reuse` rather than a silent merge. Rejected requests store
nothing, so a key is not consumed by a failure - a client can fix the quantity
and retry with the same key.

**WAL + busy timeout.** Readers (`GET /api/cart`) proceed while a writer holds
the lock instead of failing with "database is locked"; connections use a 10s
`busy_timeout`.

**Errors.** Endpoints raise `ApiError`; handlers turn it, FastAPI's
`RequestValidationError`, and any unexpected exception into the single envelope
above. Tracebacks are logged server-side only.

## Frontend decisions

**Separation.** `src/api` is the only code that knows the wire format
(snake_case DTOs, integer cents); `src/domain` holds the pure variant logic and
the camelCase model; `src/components` renders. No component looks up a SKU by
hand - they all ask `domain/variant.ts`, which is why a selection change cannot
leave a stale price, image or stock behind. There is no `any` in the source.

**Variant states.** The domain exposes three states per option value:
`available`, `out-of-stock` (a SKU exists, none left - still selectable, labelled
"Sold out", with the add button disabled so the shopper can see its price and
image) and `impossible` (no SKU for the combination - genuinely `disabled`).
Prices show as a range while the selection is incomplete, and the exact price
once it resolves.

**Quantity.** Always clamped to `[1, available]`, and re-clamped whenever the
selected SKU changes, so switching from a 6-in-stock variant to a 2-in-stock one
brings the quantity down with it.

**Duplicate submissions.** The button is disabled and `aria-busy` while a request
is in flight, an in-flight ref guards the submit path a second time, and the
idempotency key is generated per *intent*: retrying the same SKU and quantity
reuses the key (so a retry after a timeout cannot add twice), while changing the
intent mints a new one.

**Resilience without a page reload.** A failed product load shows a retry button.
A 409 `insufficient_stock` refreshes the product in place (using the server's
`available` value immediately, then re-reading the catalogue), demonstrates the
new stock, clamps the quantity, and leaves the page usable. Product loads carry a
monotonically increasing request id, so a slow response can never overwrite a
newer one. Success and failure are announced through a `role="status"` /
`role="alert"` region.

**Deep links.** The selection is mirrored into the query string and read back on
load (`/?color=sand&size=38`), which is also how an invalid combination is
reachable through the UI and covered by a test - the per-dimension filtering
normally makes it impossible to click your way into one.

**Accessibility and layout.** Option groups are `fieldset`/`legend` with
`aria-pressed` toggle buttons and visible focus rings; labels are explicit;
targets are at least 44px. The layout is a single column at ~375px and two
columns from 900px, verified in the browser at both widths.

## Tests

`app/backend/tests`:

| Module | Covers |
| --- | --- |
| `test_store.py` | SQLite write path: stock decrement, replay, key reuse, atomic rollback, rejected requests leaving no trace, 2- and 8-thread oversell races, catalogue shape |
| `test_endpoints.py` | endpoint bodies with real `Request`/`Response`: cart cookie, `Idempotency-Key` validation, replay, error mapping, SVG route |
| `test_contract.py` | the OpenAPI document: documented paths, 201 on the cart POST, the header parameter, the error envelope |
| `test_live_server.py` | a **real uvicorn server over real TCP**: health, catalogue, cookie round trip, idempotent replay, 409/404/400 envelopes, SVG. Point it at a server you started yourself with `PDP_LIVE_BASE_URL=http://127.0.0.1:8000`; otherwise it starts one (and skips, never silently, only where that is impossible outside CI) |
| `test_catalog.py`, `test_cart.py`, `test_idempotency.py`, `test_concurrency.py` | the same guarantees driven over HTTP through Starlette's `TestClient` |

The last four need an asyncio event loop. `conftest.py` probes for a usable one
once, with a timeout, and skips those modules with an explicit reason instead of
hanging the run on a host where loopback connections are blocked.

The frontend has two test layers: `pnpm test` (vitest + jsdom) for the unit and
component behaviour, and `pnpm e2e` (Playwright) which drives a real Chromium
against the production build proxied to a real uvicorn process - variant
selection, add-to-cart, cookie survival across a reload, impossible/sold-out
variants, and the 375px/1280px layouts.

Two practical notes for the e2e run:

* it starts the API with `python -m uvicorn`, so **activate `app/backend/.venv`
  first** (or otherwise have the API dependencies on PATH) when they live in a
  virtualenv;
* if the Chromium download is blocked, `E2E_CHANNEL=chrome pnpm e2e` uses an
  already-installed Chrome or Edge instead of downloading a browser.

`app/frontend/src`:

* `domain/variant.test.ts` - variant resolution, availability, quantity clamping,
  price ranges, deep-link parsing, price formatting;
* `App.test.tsx` - initial state, variant resolution updating price/image/stock,
  impossible combinations disabled, sold-out handling, quantity following the
  SKU, invalid deep link, load failure and retry;
* `components/AddToCart.test.tsx` - double-click submits once, progress state,
  success announcement, idempotency key reuse and renewal, insufficient-stock
  recovery, server validation errors.

## Limitations

No authentication, checkout, payment or deployment (explicitly out of scope). A
cart is a cookie, and stock reserved by an abandoned cart is never returned - a
real system would add reservation expiry. Idempotency keys are global rather than
per-cart, because there is no authenticated identity to scope them to. SQLite
serialises writers, which is right for one host and would be replaced by an
atomic conditional update against a shared store in a multi-host deployment.
