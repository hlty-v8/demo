#!/usr/bin/env python3
"""Task A - deterministic inventory reservation ledger.

Usage:
    python ledger.py < input.txt
    python ledger.py --input input.txt

Design notes (full write-up in README.md):
  * Parsing and execution are strictly separated: a row is fully parsed and
    validated before any state is touched, so a malformed row can never leave a
    partial state change behind.
  * Every syntactically valid row is recorded in the processed-event set -
    including rows rejected by a business rule - because the spec requires
    rejected operations to be idempotent as well.
  * State is two integers plus two hash maps, so a run is O(N) time and
    O(distinct orders + distinct event ids) space. No sorting happens per row;
    the OPEN section sorts once, at the end.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

OK = "OK"
REJECTED = "REJECTED"
DUPLICATE = "DUPLICATE"

VERBS_WITH_ORDER = frozenset({"RESERVE", "RELEASE", "SHIP"})
ALL_VERBS = VERBS_WITH_ORDER | {"RESTOCK"}

# Strict positive integer: no sign, no whitespace, no float/exponent notation.
_POSITIVE_INT = re.compile(r"[0-9]+\Z")

# Exit codes (documented in README.md).
EXIT_OK = 0
EXIT_BAD_INPUT = 2
EXIT_COMMAND_COUNT_MISMATCH = 3


class InputFormatError(ValueError):
    """The input as a whole is unusable (missing or malformed header)."""


@dataclass(frozen=True)
class Command:
    """A syntactically valid command; still subject to business rules."""

    verb: str
    event_id: str
    order_id: Optional[str]
    qty: int


@dataclass
class RunStats:
    """Counters kept for tests, diagnostics and the stderr summary."""

    commands_expected: int
    commands_seen: int
    extra_command_lines: int
    accepted: int
    rejected: int
    duplicates: int
    malformed: int


def _is_marker(value: str) -> bool:
    """Markers are non-empty printable ASCII tokens."""
    return bool(value) and value.isascii() and not any(
        ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value
    )


def _parse_positive_int(value: str) -> Optional[int]:
    if not _POSITIVE_INT.match(value):
        return None
    number = int(value)
    return number if number >= 1 else None


def parse_header(line: str) -> Tuple[int, int]:
    """Parse "<S> <N>"; raise InputFormatError if it is not usable."""
    tokens = line.split()
    if len(tokens) != 2:
        raise InputFormatError(
            f"header must be exactly '<initial_stock> <command_count>', got {line!r}"
        )
    raw_stock, raw_count = tokens
    if not _POSITIVE_INT.match(raw_stock) and raw_stock != "0":
        raise InputFormatError(f"initial stock must be an integer >= 0, got {raw_stock!r}")
    stock = int(raw_stock)
    if stock < 0:
        raise InputFormatError(f"initial stock must be >= 0, got {stock}")
    if not _POSITIVE_INT.match(raw_count):
        raise InputFormatError(f"command count must be an integer >= 1, got {raw_count!r}")
    count = int(raw_count)
    if count < 1:
        raise InputFormatError(f"command count must be >= 1, got {count}")
    return stock, count


def parse_command(tokens: Sequence[str]) -> Optional[Command]:
    """Return a Command, or None when the row is malformed.

    This never raises: malformed rows are a routine, reportable condition, not
    an exceptional one. Returning None (instead of mutating state and failing
    later) is what makes "no partial state change" structural rather than
    something the caller has to remember.
    """
    if not tokens:
        return None
    verb = tokens[0]
    if verb not in ALL_VERBS:
        return None
    expected_arity = 3 if verb == "RESTOCK" else 4
    if len(tokens) != expected_arity:
        return None
    event_id = tokens[1]
    if not _is_marker(event_id):
        return None
    if verb == "RESTOCK":
        qty = _parse_positive_int(tokens[2])
        if qty is None:
            return None
        return Command(verb=verb, event_id=event_id, order_id=None, qty=qty)
    order_id = tokens[2]
    if not _is_marker(order_id):
        return None
    qty = _parse_positive_int(tokens[3])
    if qty is None:
        return None
    return Command(verb=verb, event_id=event_id, order_id=order_id, qty=qty)


class Ledger:
    """on_hand + per-order reservations + processed event ids.

    available = on_hand - reserved is kept as a derived value so it can never
    drift out of sync with its two components.
    """

    __slots__ = ("on_hand", "total_reserved", "_reserved_by_order", "_processed")

    def __init__(self, on_hand: int) -> None:
        if on_hand < 0:
            raise ValueError("on_hand must be >= 0")
        self.on_hand = on_hand
        self.total_reserved = 0
        self._reserved_by_order: Dict[str, int] = {}
        self._processed: Dict[str, str] = {}

    @property
    def available(self) -> int:
        return self.on_hand - self.total_reserved

    def reserved_for(self, order_id: str) -> int:
        return self._reserved_by_order.get(order_id, 0)

    def is_processed(self, event_id: str) -> bool:
        return event_id in self._processed

    def mark_processed(self, event_id: str, outcome: str) -> None:
        self._processed[event_id] = outcome

    def execute(self, command: Command) -> str:
        """Apply a syntactically valid command and return OK or REJECTED.

        The caller owns idempotency; this method is never entered twice for the
        same event id.
        """
        verb = command.verb

        if verb == "RESERVE":
            if command.qty > self.available:
                return REJECTED
            assert command.order_id is not None
            self._reserved_by_order[command.order_id] = (
                self.reserved_for(command.order_id) + command.qty
            )
            self.total_reserved += command.qty
            return OK

        if verb == "RESTOCK":
            self.on_hand += command.qty
            return OK

        # RELEASE and SHIP are bounded by this order's own reservation, not by
        # the global reserved total.
        assert command.order_id is not None
        current = self.reserved_for(command.order_id)
        if command.qty > current:
            return REJECTED
        remaining = current - command.qty
        if remaining:
            self._reserved_by_order[command.order_id] = remaining
        else:
            # Dropping the key keeps the OPEN section and memory proportional to
            # the number of *open* orders rather than all orders ever seen.
            del self._reserved_by_order[command.order_id]
        self.total_reserved -= command.qty
        if verb == "SHIP":
            self.on_hand -= command.qty
        return OK

    def open_reservations(self) -> List[Tuple[str, int]]:
        return sorted(self._reserved_by_order.items())


def solve(
    text: str,
    *,
    record_malformed_event_ids: bool = False,
    open_summary: str = "count",
) -> Tuple[List[str], RunStats]:
    """Execute a whole input document.

    Returns the output lines and run statistics. Raises InputFormatError only
    when the document header itself is unusable; per-row problems are reported
    as REJECTED, as the spec requires.
    """
    raw_lines = text.splitlines()
    if not raw_lines:
        raise InputFormatError("empty input: expected a header line")

    on_hand, expected = parse_header(raw_lines[0])
    ledger = Ledger(on_hand)

    out: List[str] = []
    commands_seen = extra_lines = accepted = rejected = duplicates = malformed = 0

    for raw in raw_lines[1:]:
        if not raw.strip():
            # Blank/whitespace-only lines carry no command; they are not counted
            # against N so a trailing newline cannot break the run.
            continue
        if commands_seen >= expected:
            extra_lines += 1
            continue
        commands_seen += 1
        tokens = raw.split()
        command = parse_command(tokens)

        if command is None:
            malformed += 1
            outcome = REJECTED
            # A malformed row is outside the idempotency protocol unless the
            # caller opts in; when it does, the row behaves like any other
            # rejected operation.
            if record_malformed_event_ids and len(tokens) >= 2 and _is_marker(tokens[1]):
                event_id = tokens[1]
                if ledger.is_processed(event_id):
                    duplicates += 1
                    outcome = DUPLICATE
                else:
                    ledger.mark_processed(event_id, outcome)
        elif ledger.is_processed(command.event_id):
            # Idempotency is checked before business rules: replaying an event
            # that was originally rejected must still report DUPLICATE.
            duplicates += 1
            outcome = DUPLICATE
        else:
            outcome = ledger.execute(command)
            ledger.mark_processed(command.event_id, outcome)
            if outcome == OK:
                accepted += 1
            else:
                rejected += 1

        out.append(f"{outcome} {ledger.on_hand} {ledger.total_reserved}")

    open_rows = ledger.open_reservations()
    if open_summary == "count":
        out.append(f"OPEN {len(open_rows)}")
    elif open_summary == "sum":
        out.append(f"OPEN {sum(qty for _, qty in open_rows)}")
    # "none" prints no summary line at all (the literal reading of the prose
    # spec, where only the per-order rows are mentioned).
    for order_id, qty in open_rows:
        out.append(f"{order_id} {qty}")

    stats = RunStats(
        commands_expected=expected,
        commands_seen=commands_seen,
        extra_command_lines=extra_lines,
        accepted=accepted,
        rejected=rejected,
        duplicates=duplicates,
        malformed=malformed,
    )
    return out, stats


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ledger.py",
        description="Task A: deterministic inventory reservation ledger.",
    )
    parser.add_argument(
        "--input",
        default="-",
        help="input file, or '-' for stdin (default)",
    )
    parser.add_argument(
        "--record-malformed-event-ids",
        action="store_true",
        help=(
            "also treat the event id of a malformed row as processed, so a "
            "replayed malformed row reports DUPLICATE instead of REJECTED"
        ),
    )
    parser.add_argument(
        "--open-summary",
        choices=("count", "sum", "none"),
        default="count",
        help=(
            "meaning of the trailing OPEN line: number of open orders "
            "(default, matches the spec sample), their total reserved "
            "quantity, or no summary line"
        ),
    )
    args = parser.parse_args(argv)

    text = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")

    try:
        out, stats = solve(
            text,
            record_malformed_event_ids=args.record_malformed_event_ids,
            open_summary=args.open_summary,
        )
    except InputFormatError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT

    sys.stdout.write("\n".join(out) + "\n")

    if stats.malformed:
        print(
            f"warning: {stats.malformed} malformed command line(s) rejected",
            file=sys.stderr,
        )
    if stats.commands_seen != stats.commands_expected or stats.extra_command_lines:
        print(
            f"error: header declared N={stats.commands_expected} but "
            f"{stats.commands_seen} command line(s) were read "
            f"({stats.extra_command_lines} extra line(s) ignored)",
            file=sys.stderr,
        )
        return EXIT_COMMAND_COUNT_MISMATCH
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess
    raise SystemExit(main())
