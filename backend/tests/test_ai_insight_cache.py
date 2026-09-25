"""Covers the AI Insights DB cache (persists across reloads, keyed by dataset
cleaning_version + model_id) and the connection-check in-process TTL cache used for
passive status polling vs. the explicit 'Test Connection' button. See gemini_service.py
and api/ai.py's generate_insights/get_cached_insight for the implementation."""

import io

import pytest

from app.models.ai_insight_cache import AIInsightCache


def _make_project_with_dataset(client, name="Insight Cache Project"):
    resp = client.post("/api/projects", json={"name": name})
    project_id = resp.json()["id"]
    content = "age,income,churn\n25,50000,0\n40,60000,1\n35,55000,0\n" * 10
    upload_resp = client.post(
        f"/api/projects/{project_id}/datasets/upload",
        files={"file": ("data.csv", io.BytesIO(content.encode()), "text/csv")},
    )
    return project_id, upload_resp.json()["id"]


def test_cache_endpoint_returns_404_when_nothing_generated_yet(auth_client):
    _project_id, dataset_id = _make_project_with_dataset(auth_client)
    resp = auth_client.get("/api/ai/insights/cache", params={"dataset_id": dataset_id})
    assert resp.status_code == 404


def test_generating_an_insight_persists_it_to_the_cache_table(auth_client, monkeypatch, db_session):
    _project_id, dataset_id = _make_project_with_dataset(auth_client)

    monkeypatch.setattr(
        "app.api.ai.ai_service.generate_insight",
        lambda context: ("First generated insight.", "gemini"),
    )

    resp = auth_client.post("/api/ai/insights", json={"dataset_id": dataset_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["cached"] is False
    assert body["insight"] == "First generated insight."

    rows = db_session.query(AIInsightCache).filter(AIInsightCache.dataset_id == dataset_id).all()
    assert len(rows) == 1
    assert rows[0].insight == "First generated insight."
    assert rows[0].ai_available is True


def test_reloading_returns_the_cached_insight_without_calling_gemini_again(auth_client, monkeypatch):
    _project_id, dataset_id = _make_project_with_dataset(auth_client)

    call_count = {"n": 0}

    def fake_generate(context):
        call_count["n"] += 1
        return f"Insight #{call_count['n']}", "gemini"

    monkeypatch.setattr("app.api.ai.ai_service.generate_insight", fake_generate)

    first = auth_client.post("/api/ai/insights", json={"dataset_id": dataset_id})
    assert first.json()["cached"] is False
    assert call_count["n"] == 1

    # Simulates a page reload: the frontend hydrates via the read-only cache endpoint,
    # which must NEVER call Gemini.
    hydrate = auth_client.get("/api/ai/insights/cache", params={"dataset_id": dataset_id})
    assert hydrate.status_code == 200
    assert hydrate.json()["cached"] is True
    assert hydrate.json()["insight"] == "Insight #1"
    assert call_count["n"] == 1  # unchanged — no new Gemini call

    # A second POST without force=True must also reuse the cache instead of regenerating.
    second_post = auth_client.post("/api/ai/insights", json={"dataset_id": dataset_id})
    assert second_post.json()["cached"] is True
    assert second_post.json()["insight"] == "Insight #1"
    assert call_count["n"] == 1


def test_regenerate_with_force_bypasses_the_cache_and_calls_gemini_again(auth_client, monkeypatch):
    _project_id, dataset_id = _make_project_with_dataset(auth_client)

    call_count = {"n": 0}

    def fake_generate(context):
        call_count["n"] += 1
        return f"Insight #{call_count['n']}", "gemini"

    monkeypatch.setattr("app.api.ai.ai_service.generate_insight", fake_generate)

    auth_client.post("/api/ai/insights", json={"dataset_id": dataset_id})
    assert call_count["n"] == 1

    regenerate = auth_client.post("/api/ai/insights", json={"dataset_id": dataset_id, "force": True})
    assert regenerate.status_code == 200
    assert regenerate.json()["cached"] is False
    assert regenerate.json()["insight"] == "Insight #2"
    assert call_count["n"] == 2

    # The regenerated result becomes the new cache entry.
    hydrate = auth_client.get("/api/ai/insights/cache", params={"dataset_id": dataset_id})
    assert hydrate.json()["insight"] == "Insight #2"


def test_a_failed_generation_is_never_persisted_to_the_cache(auth_client, monkeypatch):
    """A failure must not get 'stuck' in the cache — the next attempt (even without
    force) should try Gemini again rather than silently replaying an old error."""
    from app.services import ai_service

    _project_id, dataset_id = _make_project_with_dataset(auth_client)

    def raise_unavailable(context):
        raise ai_service.AIUnavailableError("gemini down", reason="rate_limited")

    monkeypatch.setattr("app.api.ai.ai_service.generate_insight", raise_unavailable)

    resp = auth_client.post("/api/ai/insights", json={"dataset_id": dataset_id})
    assert resp.status_code == 200
    assert resp.json()["ai_available"] is False

    # Nothing was cached, so a reload's read-only hydration finds nothing either.
    hydrate = auth_client.get("/api/ai/insights/cache", params={"dataset_id": dataset_id})
    assert hydrate.status_code == 404

    # A later successful attempt (no force needed, since no cache exists) must still work.
    monkeypatch.setattr(
        "app.api.ai.ai_service.generate_insight",
        lambda context: ("Recovered insight.", "gemini"),
    )
    retry = auth_client.post("/api/ai/insights", json={"dataset_id": dataset_id})
    assert retry.json()["ai_available"] is True
    assert retry.json()["insight"] == "Recovered insight."


def test_recleaning_the_dataset_invalidates_the_cache(auth_client, monkeypatch, db_session):
    """cleaning_version is part of the cache key — re-cleaning must invalidate a stale
    insight rather than silently show insights generated against old data."""
    from app.models.dataset import Dataset

    _project_id, dataset_id = _make_project_with_dataset(auth_client)

    monkeypatch.setattr(
        "app.api.ai.ai_service.generate_insight",
        lambda context: ("Pre-clean insight.", "gemini"),
    )
    auth_client.post("/api/ai/insights", json={"dataset_id": dataset_id})

    dataset = db_session.query(Dataset).filter(Dataset.id == dataset_id).first()
    dataset.cleaning_version = (dataset.cleaning_version or 0) + 1
    db_session.commit()

    hydrate = auth_client.get("/api/ai/insights/cache", params={"dataset_id": dataset_id})
    assert hydrate.status_code == 404  # old cache_key no longer matches


# ---- gemini_service connection-check TTL cache (passive polling vs. force=True) ----


def test_check_connection_caches_result_for_passive_polling(monkeypatch):
    from app.services import gemini_service as gs

    gs._CONNECTION_CHECK_CACHE.clear()
    service = gs.GeminiService()
    service.api_key = "test-key-for-cache"

    call_count = {"n": 0}

    def fake_generate(self, *args, **kwargs):
        call_count["n"] += 1
        return "OK"

    monkeypatch.setattr(gs.GeminiService, "_generate", fake_generate)

    first = service.check_connection()
    assert first["cached"] is False
    assert call_count["n"] == 1

    second = service.check_connection()
    assert second["cached"] is True
    assert call_count["n"] == 1  # served from cache, no new Gemini call


def test_check_connection_force_true_always_bypasses_the_cache(monkeypatch):
    from app.services import gemini_service as gs

    gs._CONNECTION_CHECK_CACHE.clear()
    service = gs.GeminiService()
    service.api_key = "test-key-for-cache-2"

    call_count = {"n": 0}

    def fake_generate(self, *args, **kwargs):
        call_count["n"] += 1
        return "OK"

    monkeypatch.setattr(gs.GeminiService, "_generate", fake_generate)

    service.check_connection()
    assert call_count["n"] == 1

    forced = service.check_connection(force=True)
    assert forced["cached"] is False
    assert call_count["n"] == 2  # force=True always spends a real request


def test_settings_test_connection_endpoint_always_passes_force_true(monkeypatch):
    """The '/api/ai/status?force=true' path (Settings 'Test Connection' button) must
    reach gemini_service.check_connection with force=True, never the cached passive
    result — this is what makes it a REAL live test, not just a config check."""
    from app.services import ai_service

    captured = {}

    def fake_check_connection(force=False):
        captured["force"] = force
        return {"available": True, "reason": "ok", "provider": "gemini", "model": "gemini-2.5-flash"}

    monkeypatch.setattr("app.services.ai_service.settings.AI_PROVIDER", "gemini")
    monkeypatch.setattr("app.services.ai_service.gemini_service.check_connection", fake_check_connection)

    result = ai_service.check_status(force=True)
    assert captured["force"] is True
    assert result["available"] is True


# ---- Insights show a specific per-reason message too, not a generic one (Section 7) ----


@pytest.mark.parametrize(
    "reason,expected_snippet",
    [
        ("not_configured", "isn't set up"),
        ("invalid_key", "authenticate"),
        ("rate_limited", "rate limit"),
        ("timeout", "timed out"),
        ("unreachable", "reach"),
        ("model_not_found", "not found"),
        ("blocked", "declined"),
        ("empty_response", "empty response"),
    ],
)
def test_insights_shows_a_specific_message_per_failure_reason(auth_client, monkeypatch, reason, expected_snippet):
    from app.services.gemini_service import GeminiUnavailableError

    _project_id, dataset_id = _make_project_with_dataset(auth_client)

    # The test suite forces AI_PROVIDER=ollama by default (see conftest.py) so real
    # network calls are never made accidentally — override it here to exercise the
    # Gemini-primary path this test is actually about.
    monkeypatch.setattr("app.services.ai_service.settings.AI_PROVIDER", "gemini")

    def failing_gemini(context):
        raise GeminiUnavailableError("simulated", reason=reason)

    monkeypatch.setattr("app.services.ai_service.gemini_service.generate_insight", failing_gemini)

    resp = auth_client.post("/api/ai/insights", json={"dataset_id": dataset_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ai_available"] is False
    assert expected_snippet in body["insight"].lower()
    assert "traceback" not in body["insight"].lower()
    assert "GeminiUnavailableError" not in body["insight"]


def test_insights_preserves_gemini_reason_even_when_ollama_fallback_also_fails(auth_client, monkeypatch):
    """Regression test for a bug found during this task: ai_service.generate_insight
    re-raised AIUnavailableError with no `reason` (defaulting to 'error') when BOTH Gemini
    and the Ollama fallback failed — losing the actually-useful classification (e.g.
    'rate_limited') and showing a generic message instead."""
    from app.services.gemini_service import GeminiUnavailableError
    from app.services.ollama_service import OllamaUnavailableError

    _project_id, dataset_id = _make_project_with_dataset(auth_client)

    monkeypatch.setattr("app.services.ai_service.settings.AI_PROVIDER", "gemini")

    def failing_gemini(context):
        raise GeminiUnavailableError("gemini quota exhausted", reason="rate_limited")

    def failing_ollama(context):
        raise OllamaUnavailableError("ollama not running")

    monkeypatch.setattr("app.services.ai_service.gemini_service.generate_insight", failing_gemini)
    monkeypatch.setattr("app.services.ai_service.ollama_service.generate_insight", failing_ollama)

    resp = auth_client.post("/api/ai/insights", json={"dataset_id": dataset_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ai_available"] is False
    assert "rate limit" in body["insight"].lower()  # NOT the generic fallback message


def test_raises_model_not_found_on_404(monkeypatch):
    import httpx

    from app.services.gemini_service import GeminiService, GeminiUnavailableError

    service = GeminiService()
    service.api_key = "test-key-123"
    service.timeout = 5

    class FakeResponse:
        status_code = 404

        def json(self):
            return {"error": {"code": 404, "message": "models/bad-model is not found", "status": "NOT_FOUND"}}

    monkeypatch.setattr(httpx.Client, "post", lambda self, url, params=None, json=None: FakeResponse())
    with pytest.raises(GeminiUnavailableError) as exc_info:
        service.generate_insight({"foo": "bar"})
    assert exc_info.value.reason == "model_not_found"
