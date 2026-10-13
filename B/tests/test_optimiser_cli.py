"""End-to-end check of the documented command line on the sample files."""

from __future__ import annotations

from pathlib import Path

import optimiser

HERE = Path(__file__).resolve().parent
SAMPLE_INPUT = HERE / "sample_input.txt"
SAMPLE_OUTPUT = HERE / "sample_output.txt"


def test_sample_files_are_present():
    assert SAMPLE_INPUT.is_file()
    assert SAMPLE_OUTPUT.is_file()


def test_cli_input_flag_reproduces_the_expected_output(capsys):
    exit_code = optimiser.main(["--input", str(SAMPLE_INPUT)])
    captured = capsys.readouterr()

    assert exit_code == optimiser.EXIT_OK
    assert captured.out == SAMPLE_OUTPUT.read_text(encoding="utf-8")
    assert captured.err == ""
