"""Turn a Playwright JSON report into check-run annotations.

Why: on a public repository, job logs still require a token to download, while
check-run annotations are readable anonymously. Emitting one ``::error::`` line
per failing test (plus the first lines of its message) is therefore the only way
a failure can be diagnosed from outside the browser UI.

Usage:
    PLAYWRIGHT_JSON_OUTPUT_NAME=report.json playwright test --reporter=list --reporter=json
    python .github/scripts/annotate_playwright.py report.json
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

MAX_LINES_PER_MESSAGE = 6
MAX_LINE_LENGTH = 900


def emit(text: Any) -> None:
    for line in str(text).splitlines()[:MAX_LINES_PER_MESSAGE]:
        if line.strip():
            print(f"::error::{line[:MAX_LINE_LENGTH]}")


def walk(suite: dict[str, Any]) -> None:
    for spec in suite.get("specs", []) or []:
        for test in spec.get("tests", []) or []:
            for result in test.get("results", []) or []:
                if result.get("status") == "passed":
                    continue
                title = spec.get("title") or test.get("title") or "unknown test"
                print(f"::error::FAILED {title} [{result.get('status')}]")
                for error in result.get("errors") or []:
                    emit(error.get("message") or error.get("value") or "")
    for child in suite.get("suites", []) or []:
        walk(child)


def main(argv: list[str]) -> int:
    path = pathlib.Path(argv[1] if len(argv) > 1 else "/tmp/e2e.json")
    if not path.exists():
        print(f"::error::no Playwright JSON report at {path}")
        return 0
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:  # noqa: BLE001
        print(f"::error::could not parse the Playwright JSON report: {error!r}")
        return 0

    for suite in report.get("suites", []) or []:
        walk(suite)
    for error in report.get("errors") or []:
        emit(error.get("message") or error.get("value") or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
