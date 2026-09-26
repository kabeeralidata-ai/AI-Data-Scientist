"""Covers /api/health's git_commit field — used by scripts/check_live_server.py and the
Settings page to detect a stale, not-yet-restarted server process (see docs/PROGRESS.md
Phase 8). The actual commit value is environment-dependent (git may or may not be
available/a repo in the environment tests run in), so this only checks the shape."""


def test_health_returns_status_and_git_commit_field(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "git_commit" in body
    assert body["git_commit"] is None or isinstance(body["git_commit"], str)
