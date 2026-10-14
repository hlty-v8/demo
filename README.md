# Take-home assessment: A, B, C and the variant-PDP app

Four deliverables in one repository:

| Folder | Task | Deliverable |
| --- | --- | --- |
| [`A/`](A/README.md) | Inventory reservation ledger | Python program, tests, complexity analysis |
| [`B/`](B/README.md) | Fulfilment split optimiser | Python program, tests, complexity analysis |
| [`C/`](C/answers.md) | Xero integration review | Written answers C1-C6 with official documentation links |
| [`app/`](app/README.md) | Variant product detail page | FastAPI backend + React/TypeScript frontend + tests |

Everything here is runnable from the commands below, and every non-obvious
decision is written down (task READMEs for A and B, [`app/README.md`](app/README.md)
for the app, [`C/answers.md`](C/answers.md) for the review). A short
[verification log](#verification-log) at the end records what was actually
executed and what it produced.

---

## Prerequisites

* Python **3.11+** (developed and tested on 3.14.6; the code uses no 3.14-only features)
* Node **18+** and [pnpm](https://pnpm.io) 9+ (developed on Node 24 / pnpm 11)
* No other services: storage is SQLite and the seed data ships with the code

## Dependencies

Named in full, as requested. Nothing outside these is imported anywhere, and no
hosted service, solver or database server is involved.

| Part | Runtime | Development / test |
| --- | --- | --- |
| A, B | **Python standard library only** - no third-party imports | `pytest` |
| C | none - it is a document | - |
| app backend | `fastapi`, `uvicorn` ([`app/backend/requirements.txt`](app/backend/requirements.txt)) | `pytest`, `httpx` ([`app/backend/requirements-dev.txt`](app/backend/requirements-dev.txt)) |
| app frontend | `react`, `react-dom` | `typescript`, `vite`, `@vitejs/plugin-react`, `vitest`, `jsdom`, `@testing-library/{react,dom,jest-dom,user-event}`, `@playwright/test` ([`app/frontend/package.json`](app/frontend/package.json)) |

Versions are pinned by `app/backend/requirements*.txt`, `app/frontend/package.json`
and `app/frontend/pnpm-lock.yaml`. Everything is installable from the public
package registries (`pip`, the npm registry) with no credentials.

## Task A - inventory reservation ledger

```bash
cd A
python ledger.py < tests/sample_input.txt   # or: python ledger.py --input tests/sample_input.txt
python -m pytest tests -q
```

Reads `S N` then `N` commands (`RESERVE`, `RELEASE`, `SHIP`, `RESTOCK`) from stdin,
prints `OK` / `REJECTED` / `DUPLICATE` plus the running `on_hand reserved` totals,
then the open reservations. Single pass, O(N) time; see [`A/README.md`](A/README.md)
for the state model, the two documented ambiguities the CLI flags resolve
(`--open-summary`, `--record-malformed-event-ids`) and the full test list.

## Task B - fulfilment split optimiser

```bash
cd B
python optimiser.py < tests/sample_input.txt
python -m pytest tests -q
```

Allocates exactly `Q` units across warehouses, minimising (1) the number of
warehouses, then (2) total cost, then (3) the lexicographic allocation list. It
runs in `O(W * k* * Q)` with `k*` the minimum warehouse count - no subset
enumeration and no external solver. [`B/README.md`](B/README.md) explains the
sliding-window knapsack, the suffix-table reconstruction that resolves the
tie-break, and the complexity of each phase.

## Task C - Xero integration review

```bash
# It is a document; open it.
C/answers.md
```

Six sections (C1 connection validation, C2 fault diagnosis, C3 incremental sync,
C4 rate limiting, C5 data integrity, C6 observability and security) written
against the **current** official Xero documentation, with the 14 source links and
the SDK/API version assumptions listed at the end of the file. One point is worth
flagging up front: Xero now *does* document an `Idempotency-Key` header (with a
6-minute cache), so C5 explains that mechanism **and** why a deterministic
invoice number plus read-before-write reconciliation is still required.

## The app - variant PDP

### Backend

```bash
cd app/backend
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m uvicorn app.main:app --reload --port 8000
python -m pytest tests -q
```

Serves the catalogue, the SKU artwork and a server-authoritative cart. The seed
data creates the database on first start, so there is nothing to migrate or
download.

### Frontend

```bash
cd app/frontend
pnpm install
pnpm dev        # http://localhost:5173, proxies /api to the backend
pnpm test       # vitest: unit + component tests
pnpm build      # tsc --noEmit && vite build
pnpm e2e        # Playwright: real browser, real API (needs `pnpm exec playwright install chromium` once,
                # or `E2E_CHANNEL=chrome pnpm e2e` to use an installed Chrome instead of downloading one)
```

### Both at once (optional convenience)

```bash
app/run.sh          # macOS/Linux
app/run.ps1         # Windows PowerShell
```

Two terminals are still the clearer way to develop; the script only exists so a
reviewer can start everything with one command.

### Container (single image, single port)

```bash
docker build -t variant-pdp .
docker run --rm -p 8000:8000 -v pdp-data:/data variant-pdp
# or, equivalently:  docker compose up --build
```

One process serves both the API and the compiled SPA: the multi-stage
[`Dockerfile`](Dockerfile) builds the frontend with Node, then hands `dist/` to
the Python stage, which serves it from `/` (`PDP_STATIC_DIR`) alongside `/api`.
Same origin means the cart cookie is first-party and no proxy is involved. The
SQLite file lives on a volume and is seeded on first start. The image is built
and smoke-tested on every push by the `container` CI job. Backend contract, error model,
concurrency approach and frontend design decisions are documented in
[`app/README.md`](app/README.md) (with request/response examples and status
codes - the OpenAPI page at `/docs` is a supplement, not the contract).

---

## Architecture at a glance

```
A/ledger.py            parse -> validate -> execute, single pass, two hash maps
B/optimiser.py         cost DP (sliding window) + suffix-table reconstruction
C/answers.md           written review
app/backend/app/       catalog.py (seed)  db.py (SQLite)  models.py (contract)
                       errors.py (envelope)  store.py (all SQL)  main.py (FastAPI)
app/backend/tests/     store, endpoints, contract, plus HTTP-level suites
app/frontend/src/      api/ (transport)  domain/ (pure logic)  hooks/  components/
app/frontend/e2e/      Playwright specs (real Chromium against a real server)
Dockerfile             multi-stage: Node builds the SPA, Python serves it and the API
.github/workflows/     CI: tests on Linux, browser e2e, container build
```

Two rules are applied consistently across the repository:

* **Transport, domain and presentation are separate.** The backend never trusts
  the client for price or stock; the frontend never looks up a SKU by hand, it
  asks `domain/variant.ts`. Money is integer cents from SQLite to the rendered
  string.
* **Failures are explicit and typed.** Task A reports malformed input on stderr
  with a distinct exit code, Task B rejects out-of-constraint input rather than
  clamping it, and the API answers every failure with the same JSON envelope.

## Assumptions (the ones worth arguing about)

Each task README lists its own decisions with the tests that pin them down. The
highlights:

1. **Task A `OPEN` line.** The prose asks for open reservations in `order_id`
   order, the sample prints a single `OPEN 0`. Read as a summary line holding the
   *count*, followed by one `<order_id> <qty>` row per open order.
   `--open-summary sum|none` switches it.
2. **Task A malformed rows.** A malformed row is outside the idempotency
   protocol by default (so replaying it says `REJECTED` again);
   `--record-malformed-event-ids` opts in. Business rejections are always
   remembered, which is what the statement requires.
3. **Task B tie-break.** Ids sort as strings. Among equal-count, equal-cost
   solutions, the lexicographically smallest `(warehouse_id, qty)` list wins,
   which means preferring the smallest id at all, then the smallest quantity.
4. **App stock model.** Adding to the cart *reserves* the unit (stock is
   decremented in the same transaction). Expiry, checkout and payment are
   explicitly out of scope, as the brief allows.
5. **App idempotency keys** are global (not scoped to a cart), because carts are
   cookie-identified and there is no authenticated identity to scope them to.
   A replay returns the stored response verbatim.
6. **Out-of-stock vs impossible variants.** A combination with no SKU is
   disabled; a variant that exists but has no stock stays selectable, labelled
   "Sold out", with the add button disabled - the shopper can still see its price
   and image.

## Known limitations

* Task A/B are single-file CLIs by design; they read a whole input from stdin and
  are not streamed.
* Task B's answer is exact but only practical within the stated bounds
  (`W <= 30`, `Q <= 2000`); outside them the DP grows as stated in its README.
* The app has no authentication, checkout, payment or deployment story (out of
  scope). Cart identity is a cookie, and a cart that is never completed never
  returns its reserved stock.
* The SQLite write path is serialised by `BEGIN IMMEDIATE`, which is correct and
  sufficient for one host. A multi-host deployment would need a real database
  with row-level locking or an atomic `UPDATE ... WHERE` against a shared store.
* The HTTP-level backend tests need a working asyncio event loop (see
  [verification log](#verification-log)). The SQLite, endpoint and contract
  guarantees are additionally covered by tests that need no event loop, so this
  limitation does not leave the important behaviour untested.
* `httpx` is enough for Starlette's `TestClient` today, but FastAPI's bundled
  Starlette already warns that `httpx2` is the way forward; the dev requirements
  note it.

## AI assistance disclosure

The task statement allows AI tools provided their use is disclosed and every
decision can be explained. Disclosure, honestly and specifically:

* **Used for:** drafting the implementation, tests and documentation in this
  repository under human direction; researching **current** Xero developer
  documentation for Task C (the assistant fetched the official pages; the links
  it relied on are listed in `C/answers.md`, along with the four claims it could
  not confirm).
* **Not used for:** inventing behaviour that the task statement does not contain.
  Every ambiguity found in the statement is recorded as an explicit assumption
  with a switch or a test, not silently resolved.
* **Human review expected:** the code is written to be read and argued with. The
  tests are the argument: run them, break them, and the intent of each rule
  should be visible in the assertions. Notable judgement calls - the `OPEN`
  format, malformed-row idempotency, the Task B tie-break, the cart reservation
  model, the out-of-stock/invalid-combination UX, and the fact that Xero's
  documented `Idempotency-Key` does not remove the need for deterministic invoice
  numbers - are each stated in prose next to the code that implements them.

## Verification log

Everything below was executed. "Local" is the machine that produced this
repository (Windows, Python 3.14.6, Node 24.18, pnpm 11.7); "CI" is
`.github/workflows/ci.yml` on `ubuntu-latest`.

| Check | Command | Result |
| --- | --- | --- |
| Everything, one command | `python -m pytest -q` (repository root) | **402 passed** |
| Task A tests | `python -m pytest A/tests -q` | **110 passed** |
| Task B tests | `python -m pytest B/tests -q` | **204 passed** (incl. 160 exhaustive-search differential cases) |
| Backend tests (local) | `python -m pytest app/backend/tests -q` | **88 passed** - run per module as CI does: store 21, endpoints 12, contract 6, catalog 8, cart 16, idempotency 8, concurrency 4, live-server 7, static-site 6 |
| Backend tests (CI) | same, per module | **88 passed** on `ubuntu-latest` |
| Task A/B samples | `python A/ledger.py --input A/tests/sample_input.txt`, same for B | byte-identical to the statement's expected output |
| Frontend tests | `pnpm test` (vitest) | **29 passed** |
| Frontend typecheck | `pnpm typecheck` | clean (includes the Playwright specs) |
| Frontend build | `pnpm build` | succeeds (≈155 kB JS, ≈50 kB gzip) |
| Browser end-to-end | `pnpm e2e` (Playwright) | **4 passed locally** against Chrome and the production build proxied to a real uvicorn, and **4 passed in CI** with downloaded Chromium: variant selection, add-to-cart plus cookie survival across a reload, impossible/sold-out variants, 375px and 1280px layouts without horizontal overflow |
| Container image | `docker build` + smoke test (CI) | **passed**: image builds, becomes healthy, serves `/api/health`, serves the SPA at `/`, and an idempotent cart replay is byte-identical |
| One-command startup | `app/run.sh` executed in CI | **passed**: starts uvicorn and the Vite dev server, serves the page, and `/api/health` + `/api/products/...` answer through Vite's proxy |

### One host quirk worth recording (diagnosed and fixed)

**asyncio needs loopback, and on this host a third-party filter driver was
dropping it.** On Windows, asyncio builds its self-pipe from
`socket.socketpair()`, which is a loopback TCP connection. That connection was
being dropped *intermittently* - while, in the same minute, connections to other
local listeners (the GUI on `127.0.0.1:19387`, `redis`, `Sogou`, `WPS`) worked
fine, and rebinding to `0.0.0.0`, the LAN address or `::1` changed nothing. So
the filter was neither global nor address-based.

What it was: a **Sangfor aTrust** zero-trust driver, `aTrustNetflt_0`
(`...\Sangfor\aTrust\NetfltDriver\SdpNetFilter0.sys`), **loaded and running while
its own services were stopped** - a deny-by-default filter with no control
channel. Windows Defender's real-time protection was off, and the Windows
Firewall profiles had no rules for any of the interpreters concerned.

After uninstalling aTrust: the driver is gone from the driver list, six
consecutive probe samples over 90 seconds all passed (previously it flapped
within minutes), and the local suite went from `353 passed, 49 skipped` to
**`402 passed, 0 skipped`**, including the 36 `TestClient` HTTP tests, the 7
live-server tests and the 6 single-origin SPA tests.

The guards stay in place because they cost nothing and they protect anyone
running this repository on a machine with a similar filter:
`app/backend/tests/conftest.py` probes for a usable event loop once, with a
timeout, and skips the HTTP modules with an explicit reason instead of hanging;
`test_live_server.py` accepts `PDP_LIVE_BASE_URL` so a server started outside
pytest can be used instead of spawning one. Neither is a substitute for the CI
run, where everything executes on every push.

### The documented setup was executed at the delivered location

```bash
cd app/backend
python -m venv .venv && .venv/Scripts/activate     # Windows
pip install -r requirements-dev.txt
cd ../.. && python -m pytest -q                    # 402 passed
cd app/frontend && pnpm install --frozen-lockfile  # includes @playwright/test
pnpm typecheck && pnpm test && pnpm build          # clean, 29 passed, built
# Browser e2e. If the Chromium download is blocked on your network, point
# Playwright at an installed browser instead; activate the venv first so the
# config's `python -m uvicorn` finds the API dependencies:
E2E_CHANNEL=chrome pnpm e2e                        # 4 passed
```

`app/backend/.venv`, `node_modules/` and the SQLite files are all git-ignored, so
the repository itself stays clean.
