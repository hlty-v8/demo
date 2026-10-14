# Task A - inventory reservation ledger

Deterministic command processor for a single SKU. It tracks `on_hand`,
`reserved` (globally and per order) and the set of processed event ids, and it
prints one status line per command plus an open-reservation summary.

## Run

```bash
python ledger.py < input.txt          # stdin (the documented interface)
python ledger.py --input input.txt    # file input
pytest tests -q                          # tests
```

## Dependencies

The program imports the Python **standard library only** (`argparse`, `re`,
`sys`, `dataclasses`, `pathlib`, `typing`) - there is no third-party runtime
dependency and nothing to install to run it. The tests need `pytest`.

Exit codes: `0` success, `2` unusable header (empty input, bad `S`/`N`),
`3` the number of command lines does not match the declared `N`. Diagnostics
go to stderr; stdout stays exactly the required output.

## State model

| Piece | Type | Why |
| --- | --- | --- |
| `on_hand` | `int` | units physically in stock |
| `total_reserved` | `int` | sum of all open reservations, kept incrementally |
| `_reserved_by_order` | `dict[str, int]` | `RELEASE`/`SHIP` are bounded by *this order's* reservation, so the per-order view is not derivable from the total |
| `_processed` | `dict[str, str]` | event-id idempotency, storing the first outcome for diagnostics |

`available = on_hand - total_reserved` is always derived, never stored, so it
cannot drift out of sync with its two components. An order's entry is deleted
when its reservation reaches zero, which keeps memory proportional to *open*
orders and makes the final `OPEN` section a plain sort of the live map.

## Order of operations per command

1. **Parse and validate** the whole row (verb, arity, ASCII markers, strictly
   positive integer quantity). Nothing is mutated here.
2. If the event id is already in `_processed` 鈫?`DUPLICATE`, no state change.
3. Otherwise apply the business rule and record the outcome (including
   `REJECTED`) in `_processed`.

Two consequences that the tests pin down:

* **Idempotency is checked before business rules.** Replaying an event that was
  originally rejected prints `DUPLICATE`, not `REJECTED`, because the statement
  says rejected business operations still count as processed.
* **Malformed rows are atomic.** Because parsing is a pure function that returns
  `Command | None`, a malformed row cannot leave a partial state change behind:
  the only mutation happens after a fully successful parse.

## Assumptions and judgement calls

The task statement leaves a few things open. Each is decided explicitly, tested,
and switchable where the choice is genuinely a coin flip:

1. **`OPEN` summary meaning.** The prose says "print open reservations in
   order_id order"; the sample shows a single `OPEN 0` line. Read as: a summary
   line whose number is the *count* of open orders (0 in the sample), followed
   by one `<order_id> <qty>` line per open order, ascending by id.
   `--open-summary sum` prints the total reserved quantity instead, and
   `--open-summary none` prints only the per-order lines (the literal reading).
2. **Malformed rows and idempotency.** A malformed row does not record its event
   id by default, so replaying it prints `REJECTED` again. Passing
   `--record-malformed-event-ids` records the id when one is parseable, making
   the replay print `DUPLICATE`. Business rejections (insufficient stock,
   over-release, over-ship) are *always* recorded.
3. **Event identity is the event id alone.** Reusing an event id with a
   different verb or payload still prints `DUPLICATE`; the alternative
   (a conflict error) is not in the vocabulary the statement allows
   (`OK`/`REJECTED`/`DUPLICATE`).
4. **Strict quantities.** `qty` must match `[0-9]+` and be `>= 1`; `+5`, `1.0`,
   `1e3` and `-1` are malformed input rather than business rejections.
5. **Markers are printable ASCII, non-empty.** `orders.py`-style ids are fine;
   non-ASCII or control characters make the row malformed.
6. **Blank lines** (including a trailing newline) are ignored and do not count
   against `N`. Fewer or more command lines than `N` is reported on stderr and
   exits `3`, while still printing results for the lines that were read - a
   declared count mismatch is an input problem, not a reason to produce nothing.
7. **`N` itself is validated** (`>= 1`), and `S` may be `0` but not negative.
8. **Quantities are unbounded above** (up to `10^12` per the constraints);
   Python integers make that free, and no float arithmetic is used anywhere.

## Complexity

* Time: **O(N)**. Each command costs O(1) amortised: one `split`, one constant
  number of dict operations. The only non-constant step is the final
  `sorted()` over open orders, O(K log K) with K 鈮?N, so the total is
  O(N + K log K) = O(N log N) worst case and O(N) whenever K is bounded.
* Space: **O(N)** worst case - O(E) for processed event ids and O(O) for open
  orders, with E, O 鈮?N. Closed orders are evicted, so the per-order map holds
  only what is still open.
* I/O: stdin is read once and stdout written once. This matters at
  N = 200,000: a `print` per line is measurably slower than one buffered write.
  The scale test asserts the whole 200,000-command run stays well inside a
  linear budget (observed ~1 s including parsing).

## Tests (`tests/test_ledger.py`)

* the statement's sample, byte for byte;
* idempotency: replay of accepted, replay of rejected, replay after the ledger
  moved on, same event id across verbs, and both malformed-replay policies;
* every rejection path asserted to be atomic (no partial state change):
  insufficient stock, releasing/shipping more than the order holds, shipping an
  order with no reservation;
* 16 malformed row shapes, parameterised;
* header validation, blank lines, count mismatch, CLI exit codes and flags;
* `10**12` quantities, ASCII edge markers, determinism;
* a differential fuzz test (25 seeds) against a separately written naive
  reference model that recomputes the reserved total from scratch every step,
  so a drift between the incrementally maintained total and the map shows up;
* an invariant fuzz test (5 seeds) checking `on_hand >= reserved >= 0` and
  `sum(open) == reserved` after every single step;
* a 200,000-command linearity guard.
