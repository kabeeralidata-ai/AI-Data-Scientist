"""Benchmark CLI — runs the full real pipeline from scratch against every dataset in
backend/tests/data/ and prints a pass/fail table.

Usage (from backend/):
    ./venv/Scripts/python.exe -m tests.benchmark.run_benchmark

Exit code 0 iff every check on every dataset that has a file present actually passed AND
no dataset is missing its file (an incomplete benchmark is never reported as "passing").
"""

import sys

# Windows' default console codepage (cp1252) can't encode some characters this app's own
# messages legitimately use (e.g. em-dashes in cleaning_service's real detail text) —
# reconfigure stdout/stderr to UTF-8 (replacing anything truly unencodable) so the
# benchmark table always prints instead of crashing partway through on Windows.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from scripts.check_live_server import get_local_head, get_server_commit
from tests.benchmark import (
    checks_churn,
    checks_coffee_shop,
    checks_karachi,
    checks_retail,
    checks_retention,
    checks_telecom,
)
from tests.benchmark.harness import print_report, run_dataset_benchmark

MODULES = [
    checks_churn,
    checks_retail,
    checks_karachi,
    checks_retention,
    checks_coffee_shop,
    checks_telecom,
]


def _warn_if_a_running_dev_server_is_stale() -> None:
    """This benchmark always runs in-process against whatever code is currently checked
    out (TestClient imports app.main directly — see harness.py), so it can never be
    stale itself. But a SEPARATE long-lived dev server often also happens to be running
    on localhost:8000 (used for manual/live verification) — warn here too if it's stale,
    since that exact situation caused a real bug during Phase 6 (see
    scripts/check_live_server.py)."""
    local_head = get_local_head()
    if not local_head:
        return
    server_commit = get_server_commit("http://localhost:8000")
    if server_commit and server_commit != local_head:
        print(
            f"NOTE: a dev server on localhost:8000 is running commit {server_commit[:12]}, "
            f"not the current HEAD ({local_head[:12]}). This benchmark run itself is unaffected "
            f"(it never talks to that server), but restart it before any live-app verification.\n"
        )


def main() -> int:
    _warn_if_a_running_dev_server_is_stale()
    results = [run_dataset_benchmark(m) for m in MODULES]
    all_ok = print_report(results)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
