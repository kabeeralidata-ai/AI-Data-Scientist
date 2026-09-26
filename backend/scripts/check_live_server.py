"""Preflight check: confirms an actually-running backend server (the real long-lived
dev process on localhost:8000, as opposed to the benchmark harness, which always runs
in-process via TestClient against whatever code is currently checked out — see
tests/benchmark/harness.py) reports the same git commit as the local working tree's
HEAD.

A stale long-running dev process (started before a later code change, never restarted)
silently keeps serving OLD code — this happened during this project's Phase 6
verification: a newly-registered Jinja filter wasn't in the process the live-app reports
were generated against, and every report crashed. It was caught only because the crash
was loud; a quieter behavior change could easily have gone unnoticed.

Run this before any "live verification" (generating/inspecting reports against the real
running app, not the benchmark harness):

    python scripts/check_live_server.py [base_url]

Exit code 0: commits match, or nothing to check (no local git repo, or no server
reachable at base_url — the latter is not an error here, since not every workflow needs
a live server running). Exit code 1: a server IS running and its commit does NOT match
local HEAD — restart it (see backend/dev.py) before trusting anything it returns.
"""

import json
import subprocess
import sys
import urllib.error
import urllib.request


def get_local_head() -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def get_server_commit(base_url: str) -> str | None:
    try:
        with urllib.request.urlopen(f"{base_url}/api/health", timeout=5) as resp:
            return json.loads(resp.read()).get("git_commit")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"

    local_head = get_local_head()
    if not local_head:
        print("Could not determine the local git HEAD commit (not a git checkout?) — skipping the check.")
        return 0

    server_commit = get_server_commit(base_url)
    if server_commit is None:
        print(f"No server reachable at {base_url} (or it doesn't expose /api/health) — nothing to check.")
        return 0

    if server_commit == local_head:
        print(f"OK — live server at {base_url} is running the current commit ({local_head[:12]}).")
        return 0

    print(
        f"WARNING: live server at {base_url} is running commit {server_commit[:12]}, but the "
        f"local working tree's HEAD is {local_head[:12]}. Any 'live verification' against this "
        f"server will test STALE code, not what you think you're testing. Restart the server "
        f"(python dev.py) before continuing."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
