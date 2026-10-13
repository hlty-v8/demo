#!/usr/bin/env python3
"""Task B - fulfilment split optimiser.

Usage:
    python optimiser.py < input.txt
    python optimiser.py --input input.txt

Choose how much of an order each warehouse ships, with a strict three-level
objective:

  1. fewest warehouses used;
  2. then lowest total cost = sum(fixed_cost + allocated * unit_cost);
  3. then the lexicographically smallest allocation list, where each solution is
     compared as its list of (warehouse_id, allocated_qty) pairs sorted by
     warehouse_id.

Algorithm (full write-up in README.md):

  * Feasibility and the minimum warehouse count are a property of stock alone: a
    set of warehouses can ship exactly Q units (every used warehouse shipping at
    least one) iff the sum of its stock is >= Q. So k* - the minimum number of
    warehouses - is found by taking the largest stocks first, in O(W log W).
  * Minimum cost for exactly k* warehouses is a bounded knapsack DP over
    (warehouses used, units allocated). Because a warehouse's cost is *linear*
    in the quantity it ships, the "use this warehouse" transition is a sliding
    window minimum over the previous row, which makes each (warehouse, count)
    pair O(Q) instead of O(Q * stock).
  * The lexicographic third objective is resolved by a left-to-right greedy that
    uses suffix DP tables as a feasibility oracle: give the smallest-id
    warehouse the smallest quantity that still admits an optimal completion,
    with the smallest-id warehouses being preferred over skipping them (using a
    smaller id always makes the first differing pair smaller).
"""

from __future__ import annotations

import argparse
import re
import sys
from array import array
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

INF = 1 << 62

EXIT_OK = 0
EXIT_BAD_INPUT = 2

# Constraint bounds from the task statement; violations are input errors.
MAX_W = 30
MAX_Q = 2_000
MAX_STOCK = 2_000
MAX_COST = 1_000_000

_INT = re.compile(r"-?[0-9]+\Z")


class InputFormatError(ValueError):
    """The input does not satisfy the documented format/constraints."""


@dataclass(frozen=True)
class Warehouse:
    id: str
    stock: int
    fixed_cost: int
    unit_cost: int


def _is_marker(value: str) -> bool:
    return bool(value) and value.isascii() and not any(
        ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value
    )


def _int(token: str, what: str) -> int:
    if not _INT.match(token):
        raise InputFormatError(f"{what} must be an integer, got {token!r}")
    return int(token)


def parse(text: str) -> Tuple[List[Warehouse], int]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise InputFormatError("empty input: expected a 'W Q' header line")

    header = lines[0].split()
    if len(header) != 2:
        raise InputFormatError(f"header must be 'W Q', got {lines[0]!r}")
    w = _int(header[0], "W")
    q = _int(header[1], "Q")
    if not 1 <= w <= MAX_W:
        raise InputFormatError(f"W must be between 1 and {MAX_W}, got {w}")
    if not 1 <= q <= MAX_Q:
        raise InputFormatError(f"Q must be between 1 and {MAX_Q}, got {q}")

    rows = lines[1:]
    if len(rows) != w:
        raise InputFormatError(f"header declared W={w} but {len(rows)} warehouse row(s) were found")

    warehouses: List[Warehouse] = []
    seen = set()
    for row in rows:
        tokens = row.split()
        if len(tokens) != 4:
            raise InputFormatError(f"warehouse row must have 4 fields, got {row!r}")
        wid = tokens[0]
        if not _is_marker(wid):
            raise InputFormatError(f"warehouse_id must be a non-empty ASCII token, got {wid!r}")
        if wid in seen:
            # Ambiguous otherwise: two rows could claim the same allocation.
            raise InputFormatError(f"duplicate warehouse_id {wid!r}")
        seen.add(wid)
        stock = _int(tokens[1], f"stock of {wid}")
        fixed = _int(tokens[2], f"fixed_cost of {wid}")
        unit = _int(tokens[3], f"unit_cost of {wid}")
        if not 0 <= stock <= MAX_STOCK:
            raise InputFormatError(f"stock of {wid} must be between 0 and {MAX_STOCK}")
        if not 0 <= fixed <= MAX_COST:
            raise InputFormatError(f"fixed_cost of {wid} must be between 0 and {MAX_COST}")
        if not 0 <= unit <= MAX_COST:
            raise InputFormatError(f"unit_cost of {wid} must be between 0 and {MAX_COST}")
        warehouses.append(Warehouse(wid, stock, fixed, unit))

    return warehouses, q


def min_warehouse_count(warehouses: Sequence[Warehouse], q: int) -> Optional[int]:
    """Smallest number of warehouses that can ship q units, or None.

    Only stock matters: any q <= sum(stock) is achievable, and every used
    warehouse contributes at least one unit, so j must also satisfy j <= q.
    """
    total = 0
    for j, stock in enumerate(
        sorted((w.stock for w in warehouses if w.stock > 0), reverse=True), start=1
    ):
        total += stock
        if total >= q:
            return j if j <= q else None
    return None


def _apply_warehouse(
    old: List[List[int]],
    warehouse: Warehouse,
    q: int,
    kmax: int,
    q_cap_old: int,
) -> Tuple[List[List[int]], int]:
    """Return (new_table, new_q_cap) for "old table plus the option to use this
    warehouse".

    old[j][x] = min cost to allocate exactly x units using exactly j warehouses
    among those processed so far; x == INF marks "impossible".
    """
    new = [row[:] for row in old]
    stock = warehouse.stock
    if stock <= 0:
        return new, q_cap_old

    q_cap_new = min(q, q_cap_old + stock)
    fixed = warehouse.fixed_cost
    unit = warehouse.unit_cost

    # Descending j keeps this a 0/1 decision: old[j-1] still holds the values
    # from before this warehouse was considered.
    for j in range(kmax, 0, -1):
        if q_cap_old < j - 1:
            continue  # row is unreachable, nothing to extend
        prev = old[j - 1]
        cur = new[j]
        # window of m = qty allocated to the previous j-1 warehouses:
        #   a = q - m units here, 1 <= a <= stock  ->  m in [q-stock, q-1]
        # cost = fixed + unit*q + (prev[m] - unit*m)
        window = deque()  # (m, prev[m] - unit*m), values increasing
        for units in range(max(1, j), q_cap_new + 1):
            m = units - 1
            if m <= q_cap_old:
                pm = prev[m]
                if pm < INF:
                    value = pm - unit * m
                    while window and window[-1][1] >= value:
                        window.pop()
                    window.append((m, value))
            low = units - stock
            while window and window[0][0] < low:
                window.popleft()
            if window:
                candidate = fixed + unit * units + window[0][1]
                if candidate < cur[units]:
                    cur[units] = candidate
    return new, q_cap_new


def min_cost_table(
    warehouses: Sequence[Warehouse], q: int, kmax: int
) -> List[List[int]]:
    """Cost table after considering every warehouse, counts capped at kmax."""
    table = [[INF] * (q + 1) for _ in range(kmax + 1)]
    table[0][0] = 0
    q_cap = 0
    for warehouse in warehouses:
        table, q_cap = _apply_warehouse(table, warehouse, q, kmax, q_cap)
    return table


def suffix_tables(
    warehouses: Sequence[Warehouse], q: int, kmax: int
) -> List[List[array]]:
    """tables[i][j][x] = min cost to allocate x units with exactly j warehouses
    chosen from warehouses[i:]. Used as the reconstruction oracle.

    Stored as int64 arrays: the tables are the only large allocation in the
    program, and this keeps the worst case at ~14 MB instead of ~60 MB.
    """
    n = len(warehouses)
    tables: List[Optional[List[array]]] = [None] * (n + 1)
    base = [[INF] * (q + 1) for _ in range(kmax + 1)]
    base[0][0] = 0
    tables[n] = [array("q", row) for row in base]

    current = base
    q_cap = 0
    for i in range(n - 1, -1, -1):
        current, q_cap = _apply_warehouse(current, warehouses[i], q, kmax, q_cap)
        tables[i] = [array("q", row) for row in current]
    return tables  # type: ignore[return-value]


def reconstruct(
    warehouses: Sequence[Warehouse],
    q: int,
    count: int,
    cost: int,
    tables: Sequence[Sequence[array]],
) -> Optional[List[Tuple[str, int]]]:
    """Lexicographically smallest allocation achieving (count, cost).

    Walks warehouses in ascending id order and gives each one the smallest
    quantity that still allows an optimal completion. Using a warehouse at all
    is preferred to skipping it, because a solution whose first pair has a
    smaller id is lexicographically smaller regardless of the quantity.
    """
    remaining_count = count
    remaining_qty = q
    remaining_cost = cost
    chosen: List[Tuple[str, int]] = []

    for i, warehouse in enumerate(warehouses):
        if remaining_count == 0:
            break
        # Leave at least one unit for each warehouse still to be used.
        limit = min(warehouse.stock, remaining_qty - (remaining_count - 1))
        for allocated in range(1, limit + 1):
            need = remaining_cost - (warehouse.fixed_cost + allocated * warehouse.unit_cost)
            if need < 0:
                continue
            if tables[i + 1][remaining_count - 1][remaining_qty - allocated] == need:
                chosen.append((warehouse.id, allocated))
                remaining_count -= 1
                remaining_qty -= allocated
                remaining_cost = need
                break

    if remaining_count or remaining_qty or remaining_cost:
        return None  # unreachable if the DP tables are consistent
    return chosen


def solve(text: str) -> List[str]:
    warehouses, q = parse(text)
    # Sorted by id once, up front: the DP is order independent and the
    # reconstruction requires ascending id order.
    warehouses = sorted(warehouses, key=lambda w: w.id)

    if sum(w.stock for w in warehouses) < q:
        return ["-1"]

    count = min_warehouse_count(warehouses, q)
    if count is None:
        return ["-1"]

    if count == 1:
        # Common case: no need to build suffix tables at all.
        table = min_cost_table(warehouses, q, 1)
        cost = table[1][q]
        if cost >= INF:
            return ["-1"]
        for warehouse in warehouses:
            if warehouse.stock >= q and warehouse.fixed_cost + q * warehouse.unit_cost == cost:
                return [f"1 {cost}", f"{warehouse.id} {q}"]
        return ["-1"]

    table = min_cost_table(warehouses, q, count)
    cost = table[count][q]
    if cost >= INF:
        return ["-1"]

    tables = suffix_tables(warehouses, q, count)
    chosen = reconstruct(warehouses, q, count, cost, tables)
    if chosen is None:
        return ["-1"]

    out = [f"{count} {cost}"]
    out.extend(f"{wid} {qty}" for wid, qty in chosen)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="optimiser.py",
        description="Task B: fulfilment split optimiser.",
    )
    parser.add_argument("--input", default="-", help="input file, or '-' for stdin (default)")
    args = parser.parse_args(argv)

    text = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")

    try:
        lines = solve(text)
    except InputFormatError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT

    sys.stdout.write("\n".join(lines) + "\n")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess
    raise SystemExit(main())
