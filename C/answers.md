# Xero Integration Review — Multi-Tenant Invoice Sync

Scope: a worker-based service syncing invoices between an internal order system and Xero, storing OAuth tokens, tenant IDs and sync cursors per connected organisation. See **Assumptions** for version/SDK baseline.

---

## C1 — Connection validation: proving OAuth and tenant selection before reading invoices

Xero separates **authorisation** (the token proves the user consented) from **tenant targeting** (a header names the organisation). A token is not scoped to one organisation, so a successful token call proves nothing about tenant access. Validate in order and fail closed at each step.

### 1. Token endpoint — identity level

```http
POST https://identity.xero.com/connect/token
Authorization: Basic base64(client_id:client_secret)
Content-Type: application/x-www-form-urlencoded

grant_type=refresh_token&refresh_token=<stored>
```

**Proves:** refresh token live, client credentials correct. `200` returns `access_token` (30-min expiry) and a **rotated** `refresh_token` (60-day expiry). Assert `offline_access` is in the returned `scope` — without it there is no refresh token and the connection dies in 30 minutes. Persist the new refresh token durably before using the new access token. Refresh tokens rotate on every use; if the response is lost, Xero allows retrying with the *existing* refresh token for a **30-minute grace period**, after which the user must re-consent.

### 2. Connections endpoint — tenant authorisation level

```http
GET https://api.xero.com/connections
Authorization: Bearer <access_token>
Accept: application/json
```

**Proves:** which tenants that exact token may act on. This is the only step that proves tenant selection. Returns an array of `{id, authEventId, tenantId, tenantType, tenantName, createdDateUtc, updatedDateUtc}` — `id` is the **connection** ID (used for `DELETE`), not the tenant.

Checks:

1. **`200`, array non-empty.** Empty is valid — the user connected nothing.
2. **`tenantType == "ORGANISATION"`.** Accounting needs an organisation tenant; `PRACTICEMANAGER`/`PRACTICE` have no accounting API and often return `tenantName: null`.
3. **Intended `tenantId` present, exactly.** Keep local records in sync — connections are deleted when the user disconnects the app, loses tenant permissions, or the tenant is deleted.
4. **`authEventId` matches the `authentication_event_id` claim** decoded from the access-token JWT after a fresh consent flow (filter `?authEventId=<uuid>`); unfiltered, the response returns every tenant the user *ever* connected.
5. **Scopes as expected.** Assert `accounting.invoices` (read/write) or `accounting.invoices.read` for GET-only; add `accounting.contacts` if you resolve contacts. Broad `accounting.transactions[.read]` still works for pre-March-2026 apps but retires September 2027.

### 3. Cheap tenant-scoped probe — tenant usable, not just listed

```http
GET https://api.xero.com/api.xro/2.0/Organisation
Authorization: Bearer <access_token>
xero-tenant-id: <tenantId>
```

**Proves:** the token+tenant pair works end-to-end, and yields `Organisation.Name` / base currency for display and swap detection. A connection can exist while the org is offline (`503 Organisation offline`) or the user's role changed.

Only after all three succeed should invoice reads begin. Cache the access token per **user/connection** (one token serves several tenants), but key cursors, credentials and rate-limit budgets per `tenantId`. One tenant *type* per consent flow — accounting and Practice Manager scopes cannot be mixed in one authorisation request.

---

## C2 — Fault diagnosis: `connections` succeeds but `Invoices` returns 401/403/404

`GET /connections` is served by the identity service; `GET /api.xro/2.0/Invoices` is tenant-scoped accounting. Step 2 success proves only identity-level validity.

**0. Reproduce raw** before theorising:

```http
GET https://api.xero.com/api.xro/2.0/Invoices?page=1&pageSize=100
Authorization: Bearer <token>
xero-tenant-id: <tenantId>
Accept: application/json
```

Capture the status, body, and headers — especially `WWW-Authenticate`, `X-Rate-Limit-Problem`, `Retry-After`.

**1. `401` — "Invalid authorization credentials."**
- **Decode the access token `exp`.** Expired (30-min lifetime) ⇒ refresh once and retry; expected, not an incident.
- **Unexpired and still 401, with `WWW-Authenticate: insufficient_scope`** ⇒ the endpoint needs a granular scope you have not requested. **Most commonly misread** auth failure today: it presents as 401, not 403, so teams chase "expired token" instead of "update permissions". Prompt re-consent.
- **Repeated 401 after a successful refresh** ⇒ user disconnected the app or the refresh token was revoked. Surface re-consent; never loop.
- **401 in one environment only** ⇒ different app/client_id.

**2. `403` — "User doesn't have permission to access the resource."**
- **Tenant mismatch (primary cause).** Token valid but not authorised for the `xero-tenant-id` sent — verify against `GET /connections` for *that* token; a tenant ID stored months ago may be disconnected.
- **Header missing or malformed** also lands here.
- **User role/permission.** Consenting user lost org permissions, or holds a role that cannot authorise API access (Practice Manager, cashbook/ledger plan users cannot authorise connections).
- **Wrong tenant type** — an accounting call with a `PRACTICEMANAGER` tenant ID.
- **Environment confusion** — demo tenant ID with a production-connected token, or vice versa.
- **401 vs 403:** 401 = "cannot authenticate / scope insufficient"; 403 = "authenticated, not permitted for this resource".

**3. `404` — "The resource you have specified cannot be found."**
- **Wrong base URL or path.** Check host and version segment: `https://api.xero.com/api.xro/2.0/Invoices` (note `2.0`). A missing/wrong version or a typo like `/Invoice` returns 404 regardless of credentials — the classic configuration red herring misdiagnosed as auth.
- **Correct endpoint, wrong tenant:** a UUID that exists in the demo company does not exist in production. Looks like a data bug, is a routing bug.
- **Genuinely missing resource** (`/Invoices/{InvoiceID}`) or deleted.

**4. Environment configuration.**
- Base URLs: `identity.xero.com` (token, revocation, authorize), `api.xero.com/connections`, `api.xero.com/api.xro/2.0/...`.
- **There is no separate sandbox host.** The demo company is an ordinary organisation on the production API; only `tenantId` distinguishes it. Config that swaps a "sandbox base URL" is wrong by construction.
- **The demo company resets** 28 days after creation (or on demand), invalidating the connection — reconnect. A demo tenant that "suddenly 403/404s" is usually a reset.
- Per-environment credentials and tenant IDs must be separate secrets; dev must never read a production connection row.

**5. Statuses most often misread.**

| Status | Real cause | Misread as |
|---|---|---|
| 401 + `insufficient_scope` | Missing granular scope | Expired token |
| 403 | Tenant not authorised / missing header / user role | Expired token, "API down" |
| 404 | Wrong base URL or path; object in another tenant | Auth failure, data loss |

---

## C3 — Incremental sync for a large organisation

**Pagination.**
- `?page=1` alone returns the default page size **100**. `?page=1&pageSize=1000` uses `pageSize` *with* `page`: default 100, **max 1000**, min 1. Out-of-range values are **clamped, not rejected** (`pageSize=5000` silently yields 1000), so validate client-side and never infer effective size from what you sent.
- Responses carry `pagination{page,pageSize,pageCount,itemCount}`. Drive the loop from `pageCount`, not the superseded "short page" heuristic.
- Paged responses (Invoices and the other major endpoints) return **all line items**, removing per-invoice follow-ups.
- **High-volume guard:** a `GET /Invoices` returning (or filtering/ordering toward) more than 100k invoices is rejected with **400**. Prefer `Statuses=`, `IDs=`, `InvoiceNumbers=`, `ContactIDs=`, `summaryOnly=true`.
- Default ordering is `UpdatedDateUTC ASC, [ID] ASC`, giving stable page ordering — do not add an `order` clause that removes that determinism.

**Change windows.**
- Use the **`If-Modified-Since` header**, not `where=UpdatedDateUTC>=DateTime(...)`; Xero recommends the header explicitly.
- **Format is Xero-specific: `yyyy-mm-ddThh:mm:ss`, UTC**, second-granular — e.g. `2009-11-12T00:00:00`. This is *not* the RFC 7231 HTTP-date form (`Wed, 12 Nov 2009 00:00:00 GMT`); sending that is a common silent misconfiguration.
- The boundary is not documented as strictly inclusive or exclusive — depend on neither. Treat the window as **`[cursor − margin, run_start)`**: subtract a **safety margin** (2–5 min) and re-read that overlap every run, absorbing clock skew and second truncation; hold back records within the margin of run start and re-process next run.
- **Records modified during the run** are the real hazard: `If-Modified-Since` is evaluated per request, so later pages see a later effective window than the first — pages are not one snapshot. Use a bounded window and set the next cursor to **run start**, never the "now" at the end of a multi-minute run.
- **Silent misses:** Xero documents that some changes do **not** bump `UpdatedDateUTC` — edits to partially paid transactions generating no journal (e.g. `DueDate`, `SentToContact`) and contact fields sourced elsewhere (`Balances`, `IsSupplier`, `IsCustomer`). Those never appear in incremental pulls, so schedule a **periodic full reconciliation** (Xero recommends limiting invoice history to the last two years) rather than trusting the stream indefinitely.

**Cursor persistence.** Store per `(tenantId, entity)`: `cursor_utc`, `page`, `run_id`, `window_end`. Per page: fetch → transform → `UPSERT` rows plus an idempotent `sync_page_log(run_id, tenant_id, page, payload_hash)` → **single commit** → only then persist `page+1`.

**Why only after durable commit:** a crash after commit but before the cursor write causes a **safe re-read** (upserts are idempotent); a crash after the cursor write but before commit causes **permanent data loss** needing a full resync. At-least-once with idempotent writes beats at-most-once.

**Duplicates and partial failure.** Upsert on `(tenant_id, xero_invoice_id)`, never on `InvoiceNumber` alone. Partial success is normal: a write can return `200` with per-element `ValidationErrors`, and `summarizeErrors=false` does not fail the HTTP request — inspect each element's `HasErrors`/`ValidationErrors` before marking synced.

**Safe replay.** Resume at `(run_id, tenant_id, page)`; on restart replay from the last committed page with the *same* `If-Modified-Since` value, not a freshly computed one. Promote `cursor_utc = window_end` atomically only when `pageCount` is fully consumed and lag rows are empty; an abandoned window is never promoted.

---

## C4 — Rate limiting

**Read the headers, don't guess.** Every response carries the remaining budget: `X-DayLimit-Remaining`, `X-MinLimit-Remaining`, `X-AppMinLimit-Remaining`. On `429`, `X-Rate-Limit-Problem` names **which** limit tripped; for the **minute or daily** limit you also get `Retry-After` (seconds). Windows are **fixed** and reset at different times per tenant, so `Retry-After` is the only reliable signal for that tenant.

**Limits (per tenant, per app):** concurrent **5**; **60** calls/minute; **1,000/day** Starter, **5,000/day** higher tiers. Plus an **app-wide 10,000 calls/minute** across all tenants. Two scaling traps: the daily budget is **per tenant** (100 orgs = 100 budgets), and the app-wide ceiling is **not** per tenant, so fan-out can trip it while every tenant looks healthy. A concurrency `429` may arrive **without** `Retry-After` — treat missing as "back off briefly and retry", not "sleep a minute".

**Backoff with jitter.**
```python
delay = retry_after if retry_after else min(2 ** attempt, 60)
sleep(delay * (0.5 + random.random()))     # full jitter, never bare exponential
```
Use `Retry-After` as the floor when present; cap in-request time and let the queue retry.

**Concurrency caps and scheduling.**
- Per-tenant semaphore at **≤5** in flight, plus a **global** cap well below the app-wide ceiling.
- Per-tenant token bucket at 60/min with headroom (stop starting work at `<10` remaining) so interactive calls are not starved.
- **Daily governor:** stop backfill for a tenant at ~80% of `X-DayLimit-Remaining`; prioritise incremental over backfill.
- **Queues:** one logical queue per tenant, fair round-robin across tenants, so one noisy org cannot consume the app-wide minute allowance. Batch writes — Xero suggests ~50 elements per request (≤3.5 MB).
- **Retry budget + circuit breaker:** per tenant, cap retries at ~20% of calls or N consecutive failures; open a breaker per `tenantId` on sustained 429/5xx, half-open probe, resume. Keep a global breaker for `503`.

**Never retry:** `400` (bad payload — fix the data or mapping), `401` (refresh once, then re-authorise; never loop), `401` + `insufficient_scope` (needs a new OAuth flow), `403` (tenant not authorised, missing/malformed header, or user role), `404` (wrong URL, wrong tenant routing, or deleted resource), `412` (failed precondition, notably TLS 1.0 clients).

**Retry:** `429` (honour `Retry-After`, bounded, jittered), `500` and `503` (transient; `503` backs off in minutes, not seconds), and network timeouts — only when the same request could succeed unchanged. Classify on `status + X-Rate-Limit-Problem + WWW-Authenticate` and record the classification on a metric so misclassification is visible rather than silent.

---

## C5 — Data integrity: timeouts while creating an invoice

**Failure mode:** the create succeeds but its response is lost to a timeout; the worker treats it as "not created", retries, and duplicates the invoice. Ordinary 5xx/network retry logic is what causes it.

**Verified behaviour — the brief's premise is out of date.** The brief asks me to check whether the Accounting API lacks a general `Idempotency-Key`. **It does not lack one.** Xero publishes an official *Idempotent requests* page, and its OpenAPI specification defines an `Idempotency-Key` parameter on mutating operations:

- Header **`Idempotency-Key`** (case-insensitive) on **POST, PUT, PATCH** only. Xero caches the response; a repeat with the same key is **not re-processed** and the cached response is returned.
- Keys live **6 minutes** from the first call, then expire — a repeat after expiry is processed as a **new** key, giving no protection.
- Reusing a key against a **different** request (any change to endpoint URL, body or method) returns **400**; keys over **128 characters** return **400**. No other validation — uniqueness is the caller's responsibility, and reuse is evaluated **per app**.
- **Errors are cached too:** a request that fails internally returns the same error on replay even after the cause is fixed. Xero's guidance is to issue a **`GET` to check whether the resource already exists** and, if not, use a **new** key.
- Rate limiting applies **before** idempotency, so duplicates still consume quota. Concurrent requests within the concurrency limit are handled: the first is processed, the rest receive the cached response.
- If Xero cannot process idempotency keys at all it returns **500** until resolved, deliberately refusing to risk a duplicate.

Because the cache lasts only 6 minutes, `Idempotency-Key` alone does not cover retries hours later (queue drain, incident recovery, manual replay). Use it **plus** deterministic keys and read-before-write.

**Duplicate prevention.**
1. **Persist an attempt record before the call**, in the same transaction that marks the order "creating": `attempt_id`, `tenant_id`, `order_id`, `deterministic_key`, `invoice_number`, `state=PENDING`, `attempt_no`. Crash-safety comes from intent being durable *before* the side effect.
2. **Deterministic `InvoiceNumber`** derived from the order (e.g. `INV-{order_id}`), not a call-time sequence. It is the ACCREC unique code (max 255) and Xero enforces uniqueness per organisation, so a duplicate surfaces as a validation error rather than a second invoice. **You must supply it** — Xero auto-generates one if omitted, defeating this.
3. **Deterministic idempotency key**, generated before the call and stored. Xero recommends concatenating four UUIDs; `hash(tenant_id, order_id, attempt_epoch)` is acceptable (any string ≤128 chars).
4. **Uniqueness constraints:** `UNIQUE(tenant_id, order_id)` on attempts, `UNIQUE(tenant_id, xero_invoice_id)` once known — the database is the last defence.
5. **Durable `Reference`** (max 255) holding the order identifier, so reconciliation finds the record even if a user renumbers the invoice.

**Reconciliation — "succeeded but the response was lost".**
```text
on timeout / 5xx / unknown outcome:
  1. GET /api.xro/2.0/Invoices?InvoiceNumbers={invoice_number}
     (or ?SearchTerm=...; also IDs, ContactIDs, Statuses)
  2. Found + Reference matches -> SUCCESS: store xero_invoice_id, mark
     COMMITTED, do not POST again.
     Not found -> retry with the SAME key while the 6-minute window is open;
     after expiry, create with a NEW key.
  3. Record the outcome (found|created|ambiguous) on the attempt row.
```
Never decide "not created" from a timeout alone — the only safe negative is a completed `GET` returning zero matches. Poll with bounded attempts and backoff, allowing for propagation delay.

**Compensation / voiding.** Correct duplicates through Xero's lifecycle, not deletion: `Status=VOIDED` (or `DELETED` for drafts) via `POST /api.xro/2.0/Invoices/{InvoiceID}`, or an `ACCRECCREDIT` credit note where voiding is inappropriate (paid or part-paid). Voiding is a financial event: require an approval trail, never automate silently, and note it stays in reporting history. Match later runs on the immutable `xero_invoice_id`, not user-editable numbers.

---

## C6 — Observability and security

**Correlation identifiers.** Generate a **correlation ID per sync run** and a **request/attempt ID per Xero call**, propagated through worker, queue message, HTTP client and attempt row so logs, metrics and DB rows join. Send an outbound `X-Correlation-Id`/`X-Request-Id`; Xero's public docs do not document a request correlation header it honours, so treat any `xero-correlation-id`-style response header as best-effort support evidence, not a contract.

Log fields: `correlation_id`, `run_id`, `tenant_id` (plus a non-PII alias), `connection_id`, `attempt_id`, `http_method`, endpoint **path only**, `status_code`, `duration_ms`, `retry_attempt`, `X-Rate-Limit-Problem`, `Retry-After`, remaining-limit headers.

**Metrics** (per tenant, plus aggregate): `xero_sync_lag_seconds` (`now − cursor_utc`, the primary SLO signal); cursor position/advance counters (stalled cursors); `xero_http_requests_total{status_class,endpoint}` with explicit `401/403/404/429/5xx` counters per tenant; remaining-limit gauges and `xero_rate_limit_hits_total{problem}`; retries and `retry_budget_exhausted`; circuit-breaker state; `xero_reconciliation_total{outcome}`; `xero_duplicate_suspected_total`; `xero_token_refresh_total{result}` and token age; `xero_validation_errors_total` (must stay non-retryable, never inflating retry counters).

**Alerts.** Sync lag above threshold *for N tenants* (a global average hides one stalled tenant); sustained 401/403 for a tenant (needs re-consent — notify the tenant-facing owner, not just on-call); 429 above baseline, or any `X-Rate-Limit-Problem: Concurrent` (the cap is broken); retry budget exhausted / breaker open; **any** `ambiguous` reconciliation outcome; refresh failures (act before the 30-minute grace period expires); duplicate-suspected invoices; any observed `insufficient_scope`.

**Never log or emit.** Access, refresh and ID tokens — not in logs, traces, metric labels, error messages or crash dumps. Client secrets, `Authorization`/Basic headers, and never a full `Bearer` value (record a redacted prefix plus a keyed hash if a header must be kept). Customer PII in payload bodies (line descriptions, contact names, emails, addresses, bank numbers, tax identifiers) — log the *shape* (field names, counts) and the Xero `InvoiceID`, never the body. Watch SDK debug modes that print headers verbatim.

**Storage and rotation.** Keep tokens, client secrets and per-tenant credentials in a **secret manager** (Secrets Manager / Vault) or a column **encrypted at rest with envelope encryption** and a KMS-held key — never in code, repositories, environment defaults or plaintext DB/Redis; encrypt **refresh tokens** in particular, being the long-lived (60-day) credential. **Access tokens** last 30 minutes: refresh proactively rather than reacting to a 401. **Refresh rotation:** Xero issues a new refresh token on every refresh and invalidates the old one, so persist the new value in the transaction that consumes it, or serialise refreshes per connection with a lock; a lost rotation locks the connection out. **Revocation:** `POST identity.xero.com/connect/revocation` and/or `DELETE api.xero.com/connections/{connectionId}` — `DELETE` does **not** invalidate tokens, so use both. **Key rotation:** rotate client secrets and KMS keys on a schedule and on personnel change, with two active secrets during cutover. **Scopes:** granular scopes need fresh consent, so surface an "Update Permissions" path rather than treating `insufficient_scope` as an outage; plan for the September 2027 broad-scope retirement. Request the narrowest scope; never reuse production credentials in dev.

---

## Assumptions

- **API version:** Xero **Accounting API 2.0**, base `https://api.xero.com/api.xro/2.0`; OAuth 2.0 authorisation-code flow with `offline_access`. OAuth 1.0a is retired.
- **Spec baseline:** `xero_accounting.yaml` **version 19.1.0** (`XeroAPI/Xero-OpenAPI`).
- **SDK:** none is required by this design. Where Python is implied it is the official **`xero-python`** (`xero_python.accounting.AccountingApi`, `xero_python.identity.IdentityApi`). Its documented signatures do not surface `pageSize` or an idempotency-key argument, so pass those via client header/query configuration or call the endpoints directly, and verify the installed version. The header exists at the API level regardless.
- **Tenant model:** one connected organisation per `tenantId`; a token is cached per user/connection and reused across tenants.
- **Demo vs production:** one production API host; the demo company is an ordinary organisation distinguished only by `tenantId`, resetting 28 days after creation.
- **Idempotency:** `Idempotency-Key` exists on mutating Accounting API operations with the semantics in C5 (6-minute cache, per-app, 400 on key/request conflict, errors cached). Because the window is 6 minutes, deterministic keys plus read-before-write remain required for durable duplicate prevention.
- **`PUT /Invoices` creates; it is not an upsert keyed on `InvoiceNumber`.** A repeat `PUT` is not naturally idempotent.

---

## References

Verified against current official Xero developer documentation.

- [OAuth 2.0 authorisation code flow](https://developer.xero.com/documentation/guides/oauth2/auth-flow/) — tokens, tenants, revocation
- [Tenants](https://developer.xero.com/documentation/guides/oauth2/tenants/) — Connections endpoint and its fields
- [Scopes](https://developer.xero.com/documentation/guides/oauth2/scopes/) — granular vs deprecated broad scopes
- [Granular scopes FAQ](https://developer.xero.com/faq/granular-scopes) — 401 + `insufficient_scope`
- [API limits](https://developer.xero.com/documentation/guides/oauth2/limits/) — concurrency, minute, daily, app-wide
- [Accounting API Invoices](https://developer.xero.com/documentation/api/accounting/invoices) — `If-Modified-Since`, `page`, `pageSize`, 100k threshold
- [HTTP requests and responses](https://developer.xero.com/documentation/api/accounting/requests-and-responses/) — paging, `pagination`, `summaryOnly`
- [If-modified-since](https://developer.xero.com/documentation/best-practices/api-call-efficiencies/if-modified-since/) — format, supported endpoints
- [Idempotent requests](https://developer.xero.com/documentation/best-practices/data-integrity/idempotency/) — `Idempotency-Key` semantics
- [Accounting API response codes](https://developer.xero.com/documentation/api/accounting/responsecodes) — 400/401/403/404/412/429/500/503
- [Efficient data retrieval](https://developer.xero.com/documentation/api/efficient-data-retrieval) — incremental sync guidance
- [Development accounts](https://developer.xero.com/documentation/development-accounts) — demo company, 28-day reset
- [Official Accounting API OpenAPI spec](https://github.com/XeroAPI/Xero-OpenAPI/blob/master/xero_accounting.yaml) — `Idempotency-Key` parameter
- [Official Python SDK](https://github.com/XeroAPI/xero-python)
