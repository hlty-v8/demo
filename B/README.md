# Task B - fulfilment split optimiser

Decide how many units each warehouse ships so that exactly `Q` units are
allocated, under a strict three-level objective:

1. fewest warehouses used;
2. then lowest total cost `sum(fixed_cost + allocated_qty * unit_cost)`;
3. then the lexicographically smallest allocation list, comparing solutions as
   lists of `(warehouse_id, allocated_qty)` pairs sorted by `warehouse_id`.

## Run

```bash
python optimiser.py < input.txt          # stdin
python optimiser.py --input input.txt    # file input
pytest tests -q                          # tests
```

## Dependencies

The program imports the Python **standard library only** (`argparse`, `re`,
`sys`, `array`, `collections.deque`, `dataclasses`, `pathlib`, `typing`) - no
external optimiser, solver or third-party package is used, as the task requires.
The tests need `pytest`.

Exit codes: `0` success (including the infeasible answer `-1`), `2` malformed or
out-of-constraint input, reported on stderr. `-1` is printed only for the
genuine "cannot fulfil this order" case.

## State representation

`table[j][x]` = minimum cost to allocate exactly `x` units using exactly `j`
warehouses among those processed so far, with `INF` (a 2**62 sentinel) meaning
"impossible". Quantities are capped at `Q`, counts at `k*` (see below), so the
table is at most `(k*+1) x (Q+1)`.

## Algorithm

### 1. Feasibility and the minimum warehouse count - O(W log W)

Whether a *set* of warehouses can ship exactly `Q` units (each used warehouse
shipping at least one) depends only on stock: it is possible iff
`sum(stock) >= Q` and `count <= Q`. Costs are irrelevant to feasibility, so

> `k*` = the smallest `j` such that the sum of the `j` largest stocks is `>= Q`

found by sorting stocks descending. This is computed *before* the cost DP, which
then never needs to consider more than `k*` warehouses - a large pruning win,
because instances with big stocks have small `k*` and vice versa.

### 2. Minimum cost for exactly `k*` warehouses - O(W * k* * Q)

A bounded-knapsack style DP with one dimension per warehouse used:

```
table'[j][x] = min( table[j][x],                                   # do not use this warehouse
                    min over a in [1, min(stock, x)] of
                        table[j-1][x-a] + fixed_cost + a*unit_cost )  # ship a units from it
```

The naive transition is `O(Q * stock)` per state. Because a warehouse's cost is
**linear** in the quantity it ships, the inner minimum is a sliding-window
minimum:

```
cost = fixed_cost + unit_cost*x + min over m in [x-stock, x-1] of ( table[j-1][m] - unit_cost*m )
```

so each `(j, x)` is amortised O(1) with a monotonic deque, and one warehouse
update is O(k* * Q). Warehouses are applied one at a time, iterating `j`
downwards so the decision stays 0/1 (a warehouse cannot be used twice).

### 3. Lexicographic reconstruction - O(W * k* * Q) time and space

`k*` and the optimal cost `C*` are now known, so the third objective is "among
all allocations with exactly `k*` warehouses and cost `C*`, pick the smallest
pair list". Suffix tables `S[i][j][x]` (minimum cost using exactly `j` of the
warehouses from index `i` onwards to allocate `x` units) are the feasibility
oracle: a partial assignment can be completed optimally iff the oracle returns
exactly the remaining cost.

The greedy walks warehouses in **ascending id order** and, for each one, takes
the **smallest quantity** `a >= 1` for which `S[i+1][k-1][x-a] == remaining_cost`
(`k`, `x`, `remaining_cost` being what is still owed). Two facts make this
correct:

* **Prefer using a smaller id at all.** Comparing two equal-length pair lists,
  a solution whose first pair has the smaller id is lexicographically smaller
  *whatever* quantity follows, so if any optimal completion uses the current
  warehouse with `a >= 1`, skipping it would be worse.
* **Then prefer the smallest quantity.** With the id fixed, a smaller `a` makes
  that pair smaller and decides the comparison at the first differing position.

If no `a >= 1` admits an optimal completion, the warehouse is genuinely unused
in every optimal solution and the walk moves on. The result is emitted as
`k* C*` followed by `<warehouse_id> <qty>` per used warehouse.

## Complexity

Let `k*` be the minimum warehouse count (found in step 1), `W <= 30`,
`Q <= 2000`.

| Phase | Time | Space |
| --- | --- | --- |
| parse/validate | O(W) | O(W) |
| feasibility + `k*` | O(W log W) | O(W) |
| cost DP | O(W * k* * Q) | O(k* * Q) |
| suffix tables | O(W * k* * Q) | O(W * k* * Q) int64 |
| reconstruction | O(W * Q) worst case | O(1) extra |

Worst case `W * k* * Q = 30 * 30 * 2000 = 1.8M` deque steps per phase. The two
dimensions trade off (many warehouses required implies small stocks, which
shrinks the reachable-quantity range that the DP actually iterates), and the
suffix tables are stored as `array('q')` to keep that worst case at ~14 MB
instead of ~60 MB of boxed Python ints.

Avoiding factorisation is what makes this pass where brute force fails:
`2^30` subsets times every valid allocation is hopeless, and the obvious
`O(W * Q * stock)` DP is ~10^9 operations in Python.

## Assumptions and judgement calls

1. **Warehouse ids sort as strings** (byte order), which is what "sorted by
   warehouse_id" means for opaque ASCII markers; `w10 < w2`. Numeric ids are
   *not* reinterpreted as integers.
2. **Duplicate `warehouse_id` is an input error** (`exit 2`), because two rows
   claiming the same id would make the allocation list ambiguous.
3. **Used means `qty >= 1`.** A warehouse with stock 0 can never be used, and
   shipping 0 units from a warehouse is not "using" it.
4. **Out-of-range values are input errors**, not silently clamped: the
   documented bounds (`W <= 30`, `Q <= 2000`, `stock <= 2000`,
   `cost <= 10^6`) are enforced and reported on stderr.
5. **Ties are exact.** Costs are integers throughout; no floating point is used,
   and `INF` is an integer sentinel, so "equal cost" is decided exactly.
6. **Input rows are separated by arbitrary whitespace** and blank lines are
   ignored; the number of warehouse rows must equal `W`.
7. **`-1` is the only infeasibility signal** and covers all three cases: total
   stock below `Q`, a minimum count that cannot satisfy `Q >= count`, or a cost
   table with no reachable state (defensive; unreachable in practice).

## Tests (`tests/test_optimiser.py`)

* the statement's sample, byte for byte;
* infeasible orders, single-warehouse, zero-stock, stock-larger-than-`Q`;
* the objective *ordering*: a one-warehouse expensive answer beating a
  two-warehouse cheap one (count first), cheapest unit cost within one count,
  fixed costs participating in the cost;
* the tie-break rules: equal cost preferring the smaller id, equal cost
  preferring the smaller quantity at the earliest id, string ordering of ids;
* **differential testing** (120 + 40 random instances) against an exhaustive
  search over every subset and every valid allocation, ranked by the exact
  three-level objective, including degenerate zero-cost/zero-stock cases;
* mid-size instances cross-checked against an independently written naive
  `O(W * Q * stock)` DP, plus output self-consistency (count, cost, per-warehouse
  capacity, total quantity);
* 14 invalid-input shapes (row counts, field counts, ranges, duplicate and
  non-ASCII ids);
* scale guards: a `1`-warehouse 30x2000 instance and an adversarial 29-warehouse
  instance (observed ~1.2 s), plus determinism.
