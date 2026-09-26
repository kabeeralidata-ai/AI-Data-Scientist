import os
import subprocess
from functools import lru_cache

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


@lru_cache
def get_git_commit() -> str | None:
    """The full commit hash the currently-running process was started from — read once
    via `git rev-parse HEAD` against the repo root and cached for the process lifetime
    (a running process can't retroactively change which commit it was started from).
    Returns None when git metadata isn't available (e.g. a Docker image built without
    the .git directory) rather than raising — this is a diagnostic, never a hard
    dependency for the app to run."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None
