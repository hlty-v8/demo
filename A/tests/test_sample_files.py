"""End-to-end check of the documented command line on the sample files.

The flags are exercised the way the README documents them (``--input``), and the
output is compared with the statement's expected output stored in
``tests/sample_output.txt``. No subprocess is used: stdout is captured in-process
so the test also works in environments that restrict piped child processes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import solution

HERE = Path(__file__).resolve().parent
SAMPLE_INPUT = HERE / "sample_input.txt"
SAMPLE_OUTPUT = HERE / "sample_output.txt"


def test_sample_files_are_present():
    assert SAMPLE_INPUT.is_file()
    assert SAMPLE_OUTPUT.is_file()


def test_cli_input_flag_reproduces_the_expected_output(tmp_path, capsys):
    exit_code = solution.main(["--input", str(SAMPLE_INPUT)])
    captured = capsys.readouterr()

    assert exit_code == solution.EXIT_OK
    assert captured.out == SAMPLE_OUTPUT.read_text(encoding="utf-8")
    assert captured.err == ""


@pytest.mark.parametrize("summary", ["count", "sum", "none"])
def test_open_summary_flag_keeps_the_sample_status_lines(summary, capsys):
    exit_code = solution.main(["--input", str(SAMPLE_INPUT), "--open-summary", summary])
    captured = capsys.readouterr()

    assert exit_code == solution.EXIT_OK
    status_lines = [
        line for line in captured.out.splitlines() if line.split(" ", 1)[0] in {"OK", "REJECTED", "DUPLICATE"}
    ]
    expected_status_lines = SAMPLE_OUTPUT.read_text(encoding="utf-8").splitlines()[:-1]
    assert status_lines == expected_status_lines
