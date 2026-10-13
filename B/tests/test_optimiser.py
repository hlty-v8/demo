"""Tests for Task B - fulfilment split optimiser.

The important test here is the differential one: for small random instances the
optimiser is compared against an exhaustive search over every subset of
warehouses and every valid allocation, ranked by the exact three-level
objective. That pins down all three levels (count, cost, lexicographic) rather
than just the cost.
"""

import random
import sys
import time

import pytest

from optimiser import InputFormatError, Warehouse, min_warehouse_count, parse, solve

SAMPLE_INPUT = """\
3 7
AU 5 8 2
CN 7 20 1
US 4 3 4
"""


def text_of(q, rows):
    return f"{len(rows)} {q}\n" + "".join(f"{wid} {stock} {fixed} {unit}\n" for wid, stock, fixed, unit in rows)


# --------------------------------------------------------------------------
# Exhaustive reference implementation
# --------------------------------------------------------------------------


def _compositions(total, caps):
    """Every tuple (a1..ak) with 1 <= ai <= capsi and sum == total."""
    if not caps:
        if total == 0:
            yield ()
        return
    first, rest = caps[0], caps[1:]
    rest_min = len(rest)
    rest_max = sum(rest)
    for head in range(max(1, total - rest_max), min(first, total - rest_min) + 1):
        for tail in _compositions(total - head, rest):
            yield (head,) + tail


def brute_force(warehouses, q):
    """Exhaustive optimum under (count, cost, lexicographic) ordering."""
    n = len(warehouses)
    best = None
    for mask in range(1, 1 << n):
        idxs = [i for i in range(n) if mask >> i & 1]
        if len(idxs) > q:
            continue
        if sum(warehouses[i].stock for i in idxs) < q:
            continue
        for allocation in _compositions(q, [warehouses[i].stock for i in idxs]):
            cost = sum(
                warehouses[i].fixed_cost + a * warehouses[i].unit_cost
                for i, a in zip(idxs, allocation)
            )
            pairs = tuple(sorted((warehouses[i].id, a) for i, a in zip(idxs, allocation)))
            key = (len(idxs), cost, pairs)
            if best is None or key < best[0]:
                best = (key, cost, len(idxs), pairs)
    if best is None:
        return ["-1"]
    _, cost, count, pairs = best
    return [f"{count} {cost}"] + [f"{wid} {qty}" for wid, qty in pairs]


# --------------------------------------------------------------------------
# Statement sample and basics
# --------------------------------------------------------------------------


def test_sample_from_spec_matches_exactly():
    assert solve(SAMPLE_INPUT) == ["1 27", "CN 7"]


def test_infeasible_order_prints_minus_one():
    assert solve("2 10\nA 3 0 1\nB 4 0 1\n") == ["-1"]


def test_single_warehouse_exact_allocation():
    assert solve("1 5\nA 5 7 3\n") == ["1 22", "A 5"]


def test_zero_stock_warehouses_are_never_used():
    assert solve("3 4\nA 0 5 5\nB 4 2 1\nC 9 100 100\n") == ["1 6", "B 4"]


def test_stock_larger_than_q_is_capped_at_q():
    assert solve("1 4\nA 100 0 2\n") == ["1 8", "A 4"]


# --------------------------------------------------------------------------
# Objective ordering
# --------------------------------------------------------------------------


def test_warehouse_count_beats_total_cost():
    # One expensive warehouse (1 * 1000) must win over two cheap ones (2 * 10+1),
    # because the count objective is ranked first.
    text = text_of(10, [("X", 10, 0, 100), ("Y", 5, 0, 1), ("Z", 5, 0, 1)])
    assert solve(text) == ["1 1000", "X 10"]


def test_cost_minimised_within_the_minimum_count():
    # Both can cover Q alone: same count, so the cheaper per-unit cost wins.
    text = text_of(6, [("A", 6, 0, 5), ("B", 6, 0, 2)])
    assert solve(text) == ["1 12", "B 6"]


def test_fixed_cost_participates_in_the_cost_objective():
    # A: 100 fixed + 6*1 = 106; B: 0 fixed + 6*20 = 120.
    text = text_of(6, [("A", 6, 100, 1), ("B", 6, 0, 20)])
    assert solve(text) == ["1 106", "A 6"]


def test_two_warehouses_can_beat_one_expensive_single_source():
    # Q = 10. AU alone: 10 * 9 = 90. CN+US: (5 + 5*1) + (5 + 5*1) = 20 but uses
    # two warehouses, which loses on the first objective.
    text = text_of(10, [("AU", 10, 0, 9), ("CN", 5, 5, 1), ("US", 5, 5, 1)])
    assert solve(text) == ["1 90", "AU 10"]


# --------------------------------------------------------------------------
# Lexicographic tie-breaking
# --------------------------------------------------------------------------


def test_equal_cost_ties_prefer_the_smaller_warehouse_id():
    text = text_of(3, [("A", 5, 0, 1), ("B", 5, 0, 1)])
    assert solve(text) == ["1 3", "A 3"]


def test_equal_cost_ties_prefer_the_smaller_quantity_at_the_earliest_id():
    # Both warehouses are free, so every split costs 0 and uses two warehouses.
    # [(A,1),(B,2)] is lexicographically smaller than [(A,2),(B,1)].
    text = text_of(3, [("A", 2, 0, 0), ("B", 2, 0, 0)])
    assert solve(text) == ["2 0", "A 1", "B 2"]


def test_smallest_ids_are_used_when_counts_and_costs_tie():
    text = text_of(3, [("A", 3, 0, 0), ("B", 3, 0, 0), ("C", 3, 0, 0)])
    assert solve(text) == ["1 0", "A 3"]


def test_tie_break_prefers_using_a_smaller_id_over_skipping_it():
    # Two warehouses are needed (each stock 2, Q = 4) - here ids decide the
    # ordering of the output rows, and the mixed-cost case checks that the
    # allocation is still the cheapest.
    text = text_of(4, [("A", 2, 0, 1), ("B", 2, 0, 1), ("C", 2, 0, 5)])
    assert solve(text) == ["2 4", "A 2", "B 2"]


def test_warehouse_ids_sort_as_strings():
    # "w10" < "w2" as strings; the output order must follow that.
    text = text_of(4, [("w2", 2, 0, 0), ("w10", 2, 0, 0)])
    assert solve(text) == ["2 0", "w10 2", "w2 2"]


# --------------------------------------------------------------------------
# Differential testing against exhaustive search
# --------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(120))
def test_differential_against_brute_force(seed):
    rng = random.Random(seed)
    n = rng.randint(1, 6)
    q = rng.randint(1, 10)
    ids = rng.sample(["A", "B", "C", "D", "E", "F"], n)
    rows = [
        (wid, rng.randint(0, 5), rng.randint(0, 3), rng.randint(0, 3)) for wid in ids
    ]
    text = text_of(q, rows)

    warehouses, parsed_q = parse(text)
    expected = brute_force(warehouses, parsed_q)
    assert solve(text) == expected


@pytest.mark.parametrize("seed", range(40))
def test_differential_with_zero_costs_and_wide_stocks(seed):
    # Zero costs and zero stock exercise the degenerate corners where the whole
    # ordering collapses onto the lexicographic rule.
    rng = random.Random(5000 + seed)
    n = rng.randint(2, 6)
    q = rng.randint(1, 9)
    ids = rng.sample(["A", "B", "C", "D", "E", "F"], n)
    rows = [(wid, rng.choice([0, 0, 1, 3, 5]), 0, 0) for wid in ids]
    text = text_of(q, rows)

    warehouses, parsed_q = parse(text)
    assert solve(text) == brute_force(warehouses, parsed_q)


def _reference_min_count_and_cost(warehouses, q):
    """Independent naive DP for the first two objectives (no sliding window)."""
    n = len(warehouses)
    inf = float("inf")
    dp = [[inf] * (q + 1) for _ in range(n + 1)]
    dp[0][0] = 0
    for wh in warehouses:
        for j in range(n, 0, -1):
            for units in range(q + 1):
                best = dp[j][units]
                for a in range(1, min(wh.stock, units) + 1):
                    prev = dp[j - 1][units - a]
                    if prev < inf:
                        best = min(best, prev + wh.fixed_cost + a * wh.unit_cost)
                dp[j][units] = best
    for j in range(n + 1):
        if dp[j][q] < inf:
            return j, dp[j][q]
    return None


@pytest.mark.parametrize("seed", range(10))
def test_mid_size_cost_matches_independent_naive_dp(seed):
    rng = random.Random(9000 + seed)
    n = rng.randint(4, 10)
    ids = [f"W{i:02d}" for i in range(n)]
    rows = [
        (wid, rng.randint(0, 12), rng.randint(0, 20), rng.randint(0, 6)) for wid in ids
    ]
    q = rng.randint(1, 40)
    text = text_of(q, rows)

    warehouses, parsed_q = parse(text)
    reference = _reference_min_count_and_cost(warehouses, int(parsed_q))
    out = solve(text)

    if reference is None:
        assert out == ["-1"]
        return
    count, cost = reference
    assert out[0] == f"{count} {cost}"
    assert min_warehouse_count(warehouses, parsed_q) == count
    # Output must be a self-consistent allocation of that count and cost.
    assert len(out) == 1 + count
    total_qty = 0
    seen = set()
    for line in out[1:]:
        wid, qty = line.split()
        wh = next(w for w in warehouses if w.id == wid)
        assert wid not in seen
        seen.add(wid)
        qty = int(qty)
        assert 1 <= qty <= wh.stock
        total_qty += qty
    assert total_qty == q
    assert sum(
        next(w for w in warehouses if w.id == wid).fixed_cost
        + int(qty) * next(w for w in warehouses if w.id == wid).unit_cost
        for wid, qty in (line.split() for line in out[1:])
    ) == cost


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,reason",
    [
        ("", "empty"),
        ("3\nA 1 1 1\n", "missing Q"),
        ("2 5\nA 1 1 1\n", "row count mismatch"),
        ("1 5\nA 1 1 1\nB 1 1 1\n", "extra row"),
        ("1 5\nA 1 1\n", "too few fields"),
        ("1 5\nA 1 1 1 1\n", "too many fields"),
        ("1 5\nA x 1 1\n", "non numeric stock"),
        ("1 5\nA 1 -1 1\n", "negative fixed cost"),
        ("1 5\nA 2001 1 1\n", "stock above the documented bound"),
        ("1 5000\nA 1 1 1\n", "Q above the documented bound"),
        ("0 5\n", "W below the documented bound"),
        ("31 5\n" + "".join(f"W{i} 1 1 1\n" for i in range(31)), "W above the bound"),
        ("2 5\nA 1 1 1\nA 1 1 1\n", "duplicate warehouse id"),
        ("1 5\n\u4ed3 1 1 1\n", "non-ascii warehouse id"),
    ],
)
def test_invalid_input_is_rejected(text, reason):
    with pytest.raises(InputFormatError):
        solve(text)


def test_whitespace_and_trailing_newlines_are_tolerated():
    assert solve("\n\n2 4\n\nA 4 0 1\n\nB 4 0 1\n\n") == ["1 4", "A 4"]


# --------------------------------------------------------------------------
# Scale
# --------------------------------------------------------------------------


def test_large_instance_where_one_warehouse_covers_everything():
    rows = [(f"W{i:02d}", 2000, i, 1) for i in range(30)]
    text = text_of(2000, rows)
    started = time.perf_counter()
    out = solve(text)
    elapsed = time.perf_counter() - started
    assert out == ["1 2000", "W00 2000"]
    assert elapsed < 10.0, f"single-warehouse case took {elapsed:.2f}s"


def test_large_instance_requiring_many_warehouses():
    # Nearly every warehouse must be used: the 29 largest stocks reach 2030 >=
    # 2000, while the 28 largest only reach 1960 - so k* is 29 and the DP cannot
    # shortcut through the count dimension.
    rows = [(f"W{i:02d}", 70, 1, 3) for i in range(29)] + [("W29", 40, 1, 3)]
    text = text_of(2000, rows)
    started = time.perf_counter()
    out = solve(text)
    elapsed = time.perf_counter() - started
    count = int(out[0].split()[0])
    assert count == 29, out[0]
    assert sum(int(line.split()[1]) for line in out[1:]) == 2000
    assert elapsed < 45.0, f"hard instance took {elapsed:.2f}s"
    print(f"\nhard instance: {elapsed:.2f}s, {len(out) - 1} warehouses")


def test_solution_is_deterministic():
    text = text_of(7, [("AU", 5, 8, 2), ("CN", 7, 20, 1), ("US", 4, 3, 4)])
    assert solve(text) == solve(text)
