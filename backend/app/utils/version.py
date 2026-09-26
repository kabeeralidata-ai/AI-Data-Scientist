import os
import subprocess

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def get_git_commit() -> str | None:
    """The repo's current HEAD commit, read fresh via `git rev-parse HEAD` on every
    call — deliberately NOT cached. A `git commit` changes HEAD without touching any
    file content, so a cache seeded from an earlier request would keep reporting a
    now-stale commit hash even though the process is serving identical code (it hasn't
    actually gone stale at all — the cache would just be lying about which commit
    that code corresponds to). The actual, valuable staleness signal this guards
    against is a DEPLOYED process that hasn't pulled/restarted after new code landed —
    e.g. in CI or a container — where the check needs to compare against whatever HEAD
    genuinely is right now, not whatever it was when the process started. Returns None
    when git metadata isn't available (e.g. a Docker image built without the .git
    directory) rather than raising — this is a diagnostic, never a hard dependency for
    the app to run."""
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
