"""Tests for Task A - inventory reservation ledger.

Coverage map:
  * the spec's own sample, byte for byte;
  * idempotency: replay of accepted, replay of rejected, replay after the state
    moved on;
  * every business rule's rejection path, asserted to be atomic (no partial
    state change);
  * malformed input of every shape, plus the replay-of-malformed ambiguity;
  * header validation and CLI exit codes;
  * large values (10**12), determinism, a differential fuzz test against an
    independently written reference model, and an O(N) performance guard.
"""

import io
import random
import sys
import time

import pytest

from solution import (
    DUPLICATE,
    EXIT_BAD_INPUT,
    EXIT_COMMAND_COUNT_MISMATCH,
    EXIT_OK,
    OK,
    REJECTED,
    InputFormatError,
    solve,
)

SAMPLE_INPUT = """\
10 6
RESERVE e1 o100 4
RESERVE e2 o200 7
SHIP e3 o100 2
RELEASE e4 o100 2
RESTOCK e5 3
RESERVE e2 o999 1
"""

SAMPLE_OUTPUT = """\
OK 10 4
REJECTED 10 4
OK 8 2
OK 8 0
OK 11 0
DUPLICATE 11 0
OPEN 0
"""


def out_lines(text: str, **kwargs):
    lines, stats = solve(text, **kwargs)
    return lines, stats


def statuses(text: str, **kwargs):
    """Only the per-command status lines (the OPEN section is dropped)."""
    lines, stats = solve(text, **kwargs)
    return lines[: stats.commands_seen]


# --------------------------------------------------------------------------
# Spec sample
# --------------------------------------------------------------------------


def test_sample_from_spec_matches_exactly():
    lines, stats = out_lines(SAMPLE_INPUT)
    assert "\n".join(lines) + "\n" == SAMPLE_OUTPUT
    assert (stats.accepted, stats.rejected, stats.duplicates, stats.malformed) == (4, 1, 1, 0)


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


def test_replay_of_accepted_event_returns_duplicate_and_changes_nothing():
    lines, _ = out_lines("5 3\nRESERVE a o1 3\nRESERVE a o1 3\nRESERVE b o1 2\n")
    assert lines[0] == "OK 5 3"
    assert lines[1] == "DUPLICATE 5 3"
    assert lines[2] == "OK 5 5"  # available was 2, so 2 more still fits


def test_replay_of_rejected_business_event_returns_duplicate():
    # Spec: rejected business operations still count as processed, so the replay
    # must report DUPLICATE rather than REJECTED again.
    lines, stats = out_lines("1 3\nRESERVE e1 o1 5\nRESERVE e1 o1 5\nRESTOCK e2 1\n")
    assert lines[0] == "REJECTED 1 0"
    assert lines[1] == "DUPLICATE 1 0"
    assert lines[2] == "OK 2 0"
    assert stats.duplicates == 1


def test_replay_after_state_moved_on_is_still_duplicate():
    # e1 reserved 4; it is not replayed until after stock changed. The outcome
    # must depend only on the event id, not on the current ledger state.
    lines, _ = out_lines(
        "4 4\n"
        "RESERVE e1 o1 4\n"
        "SHIP e2 o1 4\n"
        "RESTOCK e3 100\n"
        "RESERVE e1 o2 4\n"
    )
    assert lines[0] == "OK 4 4"
    assert lines[1] == "OK 0 0"
    assert lines[2] == "OK 100 0"
    assert lines[3] == "DUPLICATE 100 0"


def test_same_event_id_across_different_verbs_is_duplicate():
    # Documented decision: the event id, not the payload, defines identity.
    lines, _ = out_lines("5 3\nRESERVE e1 o1 1\nRELEASE e1 o1 1\nRESTOCK e1 2\n")
    assert lines[0] == "OK 5 1"
    assert lines[1] == "DUPLICATE 5 1"
    assert lines[2] == "DUPLICATE 5 1"


# --------------------------------------------------------------------------
# Business rules and atomicity
# --------------------------------------------------------------------------


def test_reserve_rejected_when_insufficient_and_leaves_no_partial_change():
    assert statuses("10 4\nRESERVE a o1 6\nRESERVE b o2 5\nRESERVE c o1 4\nRESERVE d o2 4\n") == [
        "OK 10 6",
        "REJECTED 10 6",
        "OK 10 10",
        "REJECTED 10 10",
    ]


def test_release_bounded_by_that_orders_reservation_not_the_global_total():
    # Global reserved is 7, but o1 only holds 2: releasing 3 must be rejected.
    assert statuses(
        "20 5\nRESERVE a o1 2\nRESERVE b o2 5\nRELEASE c o1 3\nRELEASE d o1 2\nRELEASE e o3 1\n"
    ) == ["OK 20 2", "OK 20 7", "REJECTED 20 7", "OK 20 5", "REJECTED 20 5"]


def test_ship_reduces_on_hand_and_that_orders_reservation():
    assert statuses("10 4\nRESERVE a o1 4\nSHIP b o1 3\nSHIP c o1 3\nSHIP d o1 1\n") == [
        "OK 10 4",
        "OK 7 1",
        "REJECTED 7 1",
        "OK 6 0",
    ]


def test_ship_without_reservation_is_rejected():
    assert statuses("10 2\nSHIP a o1 1\nRELEASE b o1 1\n") == ["REJECTED 10 0", "REJECTED 10 0"]


def test_restock_only_touches_on_hand():
    assert statuses("0 3\nRESTOCK a 5\nRESERVE b o1 3\nRESTOCK c 1\n") == [
        "OK 5 0",
        "OK 5 3",
        "OK 6 3",
    ]


def test_reservations_accumulate_across_events_for_the_same_order():
    assert statuses("10 4\nRESERVE a o1 2\nRESERVE b o1 3\nSHIP c o1 4\nRELEASE d o1 1\n") == [
        "OK 10 2",
        "OK 10 5",
        "OK 6 1",
        "OK 6 0",
    ]


def test_reserve_exactly_available_is_accepted():
    assert statuses("5 2\nRESERVE a o1 5\nRESERVE b o2 1\n") == ["OK 5 5", "REJECTED 5 5"]


# --------------------------------------------------------------------------
# Malformed input
# --------------------------------------------------------------------------

MALFORMED_ROWS = [
    ("BOGUS e1 o1 1", "unknown verb"),
    ("reserve e1 o1 1", "verb is case sensitive"),
    ("RESERVE e1 o1", "missing qty"),
    ("RESERVE e1 o1 1 extra", "extra token"),
    ("RESERVE e1 1", "missing order id"),
    ("RESTOCK e1", "restock missing qty"),
    ("RESTOCK e1 1 extra", "restock extra token"),
    ("RESERVE e1 o1 0", "zero qty is not a positive integer"),
    ("RESERVE e1 o1 -1", "negative qty"),
    ("RESERVE e1 o1 +5", "explicit sign"),
    ("RESERVE e1 o1 1.0", "float qty"),
    ("RESERVE e1 o1 1e3", "exponent notation"),
    ("RESERVE e1 o1 abc", "non-numeric qty"),
    ("RESERVE e1 订单 1", "non-ascii order id"),
    ("RESERVE 事件 o1 1", "non-ascii event id"),
    ("RESERVE e1 o1 1 2", "too many tokens"),
]


@pytest.mark.parametrize("row,reason", MALFORMED_ROWS, ids=[r[1] for r in MALFORMED_ROWS])
def test_malformed_row_is_rejected_and_state_is_untouched(row, reason):
    # The malformed row is rejected but does not consume the run: the following
    # valid command still executes against an unchanged ledger.
    text = f"10 2\n{row}\nRESERVE good o9 4\n"
    assert statuses(text) == ["REJECTED 10 0", "OK 10 4"], reason
    assert solve(text)[1].malformed == 1


@pytest.mark.parametrize("row,reason", MALFORMED_ROWS, ids=[r[1] for r in MALFORMED_ROWS])
def test_malformed_replay_default_ignores_event_id(row, reason):
    assert statuses(f"10 2\n{row}\n{row}\n") == ["REJECTED 10 0", "REJECTED 10 0"], reason


@pytest.mark.parametrize("row,reason", MALFORMED_ROWS[:6], ids=[r[1] for r in MALFORMED_ROWS[:6]])
def test_malformed_replay_records_event_id_when_opted_in(row, reason):
    assert statuses(f"10 2\n{row}\n{row}\n", record_malformed_event_ids=True) == [
        "REJECTED 10 0",
        "DUPLICATE 10 0",
    ], reason


def test_blank_lines_are_ignored_so_a_trailing_newline_is_harmless():
    lines, stats = out_lines("5 1\n\nRESERVE a o1 1\n\n")
    assert lines == ["OK 5 1", "OPEN 1", "o1 1"]
    assert stats.commands_seen == 1


def test_extra_command_lines_beyond_n_are_ignored_and_reported():
    lines, stats = out_lines("5 1\nRESERVE a o1 1\nRESERVE b o1 1\n")
    assert lines == ["OK 5 1", "OPEN 1", "o1 1"]
    assert stats.extra_command_lines == 1


def test_fewer_lines_than_n_is_reported_not_guessed():
    lines, stats = out_lines("5 3\nRESERVE a o1 1\n")
    assert lines == ["OK 5 1", "OPEN 1", "o1 1"]
    assert stats.commands_seen == 1
    assert stats.commands_expected == 3


# --------------------------------------------------------------------------
# Header validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    ["", "10", "10 1 2", "ten 1", "10 n", "10 0", "-1 1", "1.5 1", "  "],
)
def test_invalid_header_raises(header):
    with pytest.raises(InputFormatError):
        solve(f"{header}\nRESERVE a o1 1\n")


def test_empty_input_raises():
    with pytest.raises(InputFormatError):
        solve("")


def test_zero_initial_stock_is_valid():
    lines, _ = solve("0 2\nRESTOCK a 3\nRESERVE b o1 3\n")
    assert lines == ["OK 3 0", "OK 3 3", "OPEN 1", "o1 3"]


# --------------------------------------------------------------------------
# OPEN section
# --------------------------------------------------------------------------


def test_open_section_is_sorted_by_order_id_and_omits_settled_orders():
    lines, _ = solve(
        "20 5\n"
        "RESERVE a oB 3\n"
        "RESERVE b oA 2\n"
        "RESERVE c oC 1\n"
        "SHIP d oB 3\n"
        "RELEASE e oC 1\n"
    )
    assert lines[5:] == ["OPEN 1", "oA 2"]


def test_open_when_no_reservations_is_exactly_the_spec_sample_shape():
    lines, _ = solve("10 1\nRESTOCK a 1\n")
    assert lines[-1] == "OPEN 0"
    assert len(lines) == 2


def test_open_summary_variants():
    text = "20 2\nRESERVE a oA 3\nRESERVE b oB 4\n"
    assert solve(text)[0][2:] == ["OPEN 2", "oA 3", "oB 4"]
    assert solve(text, open_summary="sum")[0][2:] == ["OPEN 7", "oA 3", "oB 4"]
    assert solve(text, open_summary="none")[0][2:] == ["oA 3", "oB 4"]


# --------------------------------------------------------------------------
# Scale, determinism, differential fuzz
# --------------------------------------------------------------------------


def test_large_values_are_exact():
    big = 10**12
    lines, _ = solve(
        f"{big} 4\n"
        f"RESERVE a o1 {big}\n"
        f"RESERVE b o2 1\n"
        f"SHIP c o1 {big - 1}\n"
        f"RESTOCK d {big}\n"
    )
    assert lines[0] == f"OK {big} {big}"
    assert lines[1] == f"REJECTED {big} {big}"
    assert lines[2] == f"OK 1 {1}"
    assert lines[3] == f"OK {1 + big} 1"


def test_ascii_marker_extremes_are_accepted():
    lines, _ = solve("5 2\nRESERVE !#$%^&*() o1 1\nRESERVE e2 ~ 1\n")
    assert lines[0] == "OK 5 1"
    assert lines[1] == "OK 5 2"
    assert lines[2] == "OPEN 2"
    assert lines[3:] == ["o1 1", "~ 1"]


def test_run_is_deterministic():
    assert solve(SAMPLE_INPUT)[0] == solve(SAMPLE_INPUT)[0]


def _reference_model(text: str):
    """Deliberately independent, naive implementation of the spec.

    It recomputes the global reserved total from the per-order map on every
    step (the solution increments a counter instead), so a drift between the
    two would show up as a diff.
    """
    lines = text.splitlines()
    on_hand, expected = (int(x) for x in lines[0].split())
    reserved = {}
    processed = set()
    out = []
    for raw in lines[1 : 1 + expected]:
        verb, *rest = raw.split()
        eid = rest[0]
        if eid in processed:
            out.append(f"DUPLICATE {on_hand} {sum(reserved.values())}")
            continue
        processed.add(eid)
        if verb == "RESTOCK":
            on_hand += int(rest[1])
            out.append(f"OK {on_hand} {sum(reserved.values())}")
            continue
        oid, qty = rest[1], int(rest[2])
        if verb == "RESERVE":
            if qty <= on_hand - sum(reserved.values()):
                reserved[oid] = reserved.get(oid, 0) + qty
                out.append(f"OK {on_hand} {sum(reserved.values())}")
            else:
                out.append(f"REJECTED {on_hand} {sum(reserved.values())}")
            continue
        if qty > reserved.get(oid, 0):
            out.append(f"REJECTED {on_hand} {sum(reserved.values())}")
            continue
        reserved[oid] -= qty
        if verb == "SHIP":
            on_hand -= qty
        if reserved[oid] == 0:
            del reserved[oid]
        out.append(f"OK {on_hand} {sum(reserved.values())}")
    open_orders = sorted(reserved.items())
    out.append(f"OPEN {len(open_orders)}")
    out.extend(f"{oid} {qty}" for oid, qty in open_orders)
    return out


@pytest.mark.parametrize("seed", range(25))
def test_differential_fuzz_against_reference_model(seed):
    rng = random.Random(seed)
    rows = []
    n = rng.randint(1, 200)
    for _ in range(n):
        verb = rng.choice(["RESERVE", "RELEASE", "SHIP", "RESTOCK"])
        # Repeat ids aggressively so idempotency paths are hit often.
        eid = f"e{rng.randint(0, max(1, n // 3))}"
        if verb == "RESTOCK":
            rows.append(f"{verb} {eid} {rng.randint(1, 6)}")
        else:
            rows.append(f"{verb} {eid} o{rng.randint(0, 3)} {rng.randint(1, 6)}")
    text = f"{rng.randint(0, 12)} {n}\n" + "\n".join(rows) + "\n"

    lines, stats = solve(text)
    assert lines == _reference_model(text)
    assert stats.malformed == 0


@pytest.mark.parametrize("seed", range(5))
def test_ledger_invariants_hold_after_every_step(seed):
    rng = random.Random(1000 + seed)
    rows = []
    for i in range(300):
        verb = rng.choice(["RESERVE", "RELEASE", "SHIP", "RESTOCK"])
        if verb == "RESTOCK":
            rows.append(f"{verb} e{i} {rng.randint(1, 9)}")
        else:
            rows.append(f"{verb} e{i} o{rng.randint(0, 4)} {rng.randint(1, 9)}")
    text = f"{rng.randint(0, 15)} {len(rows)}\n" + "\n".join(rows) + "\n"

    # Re-derive the running totals from the printed output and check that the
    # bookkeeping identities never break: reserved totals never exceed on_hand,
    # and the per-order sum equals the reported reserved total.
    lines, _ = solve(text, open_summary="none")
    per_order = {}
    for line in lines:
        parts = line.split()
        if parts[0] in ("OK", "REJECTED", "DUPLICATE"):
            on_hand, reserved = int(parts[1]), int(parts[2])
            assert on_hand >= 0
            assert reserved >= 0
            assert on_hand - reserved >= 0
        else:
            per_order[parts[0]] = int(parts[1])
    assert sum(per_order.values()) == reserved


def test_two_hundred_thousand_commands_stay_linear():
    n = 200_000
    rows = []
    for i in range(n):
        if i % 4 == 0:
            rows.append(f"RESERVE e{i} o{i % 5000} 1")
        elif i % 4 == 1:
            rows.append(f"SHIP e{i} o{i % 5000} 1")
        elif i % 4 == 2:
            rows.append(f"RELEASE e{i} o{i % 5000} 1")
        else:
            rows.append(f"RESTOCK e{i} 1")
    text = f"{n} {n}\n" + "\n".join(rows) + "\n"

    started = time.perf_counter()
    lines, stats = solve(text)
    elapsed = time.perf_counter() - started

    # n status lines + 1 OPEN summary + one row per still-open order.
    assert stats.commands_seen == n
    assert lines[n].startswith("OPEN ")
    assert len(lines) == n + 1 + int(lines[n].split()[1])
    assert elapsed < 10.0, f"O(N) expectation violated: {elapsed:.2f}s for {n} commands"
    print(f"\n{n} commands in {elapsed:.2f}s")


# --------------------------------------------------------------------------
# CLI contract
# --------------------------------------------------------------------------


def test_cli_stdin_stdout_and_exit_code(monkeypatch, capsys):
    import solution

    monkeypatch.setattr(sys, "stdin", io.StringIO(SAMPLE_INPUT))
    assert solution.main([]) == EXIT_OK
    captured = capsys.readouterr()
    assert captured.out == SAMPLE_OUTPUT
    assert captured.err == ""


def test_cli_exit_code_for_bad_header(monkeypatch, capsys):
    import solution

    monkeypatch.setattr(sys, "stdin", io.StringIO("nonsense\n"))
    assert solution.main([]) == EXIT_BAD_INPUT
    assert "error:" in capsys.readouterr().err


def test_cli_exit_code_for_command_count_mismatch(monkeypatch, capsys):
    import solution

    monkeypatch.setattr(sys, "stdin", io.StringIO("5 3\nRESERVE a o1 1\n"))
    assert solution.main([]) == EXIT_COMMAND_COUNT_MISMATCH
    captured = capsys.readouterr()
    assert captured.out.startswith("OK 5 1")
    assert "declared N=3" in captured.err


def test_cli_reads_file_and_open_summary_flag(tmp_path, capsys):
    import solution

    path = tmp_path / "input.txt"
    path.write_text("20 1\nRESERVE a o1 3\n", encoding="utf-8")
    assert solution.main(["--input", str(path), "--open-summary", "sum"]) == EXIT_OK
    assert capsys.readouterr().out == "OK 20 3\nOPEN 3\no1 3\n"
