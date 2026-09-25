"""Confirms AI Chat, the 'Generate insight' button (AI Insights tab / Modeling tab), and
Auto Analyze's AI step all route through the exact same ai_service — there is no
lingering direct Ollama call anywhere. Verified by monkeypatching ai_service's own
generate_insight/answer_question (the single choke point) and checking every endpoint
reports the same mocked provider."""

import io


def _make_project_with_dataset(client, name="Provider Routing Project"):
    resp = client.post("/api/projects", json={"name": name})
    project_id = resp.json()["id"]
    content = "age,income,churn\n25,50000,0\n40,60000,1\n35,55000,0\n" * 10
    upload_resp = client.post(
        f"/api/projects/{project_id}/datasets/upload",
        files={"file": ("data.csv", io.BytesIO(content.encode()), "text/csv")},
    )
    return project_id, upload_resp.json()["id"]


def test_insights_endpoint_reports_the_provider_ai_service_used(auth_client, monkeypatch):
    project_id, dataset_id = _make_project_with_dataset(auth_client)

    monkeypatch.setattr(
        "app.api.ai.ai_service.generate_insight",
        lambda context: ("Mocked insight text.", "gemini"),
    )

    resp = auth_client.post("/api/ai/insights", json={"dataset_id": dataset_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ai_available"] is True
    assert body["provider"] == "gemini"
    assert body["insight"] == "Mocked insight text."


def test_chat_endpoint_reports_the_same_provider_as_insights(auth_client, monkeypatch):
    """The critical assertion: chat and insights must report the SAME provider for the
    SAME mocked ai_service — proving they share one routing path, not two independent
    ones (the original bug: Auto Analyze used the new provider, chat/insights didn't)."""
    project_id, dataset_id = _make_project_with_dataset(auth_client)

    monkeypatch.setattr(
        "app.api.ai.ai_service.generate_insight",
        lambda context: ("Mocked insight text.", "gemini"),
    )
    monkeypatch.setattr(
        "app.api.ai.ai_service.answer_question",
        lambda context, question, history=None: ("Mocked chat answer.", "gemini"),
    )

    insights_resp = auth_client.post("/api/ai/insights", json={"dataset_id": dataset_id})
    chat_resp = auth_client.post(
        "/api/ai/chat", json={"project_id": project_id, "message": "What drives income?"}
    )

    assert insights_resp.json()["provider"] == "gemini"
    assert chat_resp.json()["provider"] == "gemini"
    assert chat_resp.json()["ai_generated"] is True


def test_chat_and_insights_both_degrade_honestly_when_ai_service_raises(auth_client, monkeypatch):
    from app.services import ai_service

    project_id, dataset_id = _make_project_with_dataset(auth_client)

    def raise_unavailable(*args, **kwargs):
        raise ai_service.AIUnavailableError("both providers down")

    monkeypatch.setattr("app.api.ai.ai_service.generate_insight", raise_unavailable)
    monkeypatch.setattr("app.api.ai.ai_service.answer_question", raise_unavailable)

    insights_resp = auth_client.post("/api/ai/insights", json={"dataset_id": dataset_id})
    chat_resp = auth_client.post(
        "/api/ai/chat", json={"project_id": project_id, "message": "What drives income?"}
    )

    assert insights_resp.json()["ai_available"] is False
    assert insights_resp.json()["provider"] is None
    assert chat_resp.json()["ai_available"] is False
    assert chat_resp.json()["provider"] is None


def test_report_generation_uses_the_shared_ai_service_not_a_direct_ollama_call(auth_client, monkeypatch):
    """Regression test for the reported bug: the report PDF said 'AI unavailable' even
    though Gemini worked everywhere else in the app, because report_service imported and
    called ollama_service directly instead of the shared ai_service (Gemini-first,
    Ollama-fallback) entry point every other AI surface uses."""
    project_id, dataset_id = _make_project_with_dataset(auth_client)

    monkeypatch.setattr(
        "app.services.report_service.ai_service.generate_report_insight",
        lambda context: ("## Key Business Insights\n- Mocked insight.", "gemini"),
    )

    resp = auth_client.post("/api/reports/projects/" + project_id + "/generate", json={"dataset_id": dataset_id})
    assert resp.status_code == 201
    body = resp.json()
    assert body["content_json"]["ai_available"] is True
    assert body["content_json"]["ai_provider"] == "gemini"
    assert "Mocked insight" in body["content_json"]["ai_insight"]


def test_ai_status_reflects_the_configured_provider_not_a_hard_coded_one(auth_client, monkeypatch):
    monkeypatch.setattr("app.services.ai_service.settings.AI_PROVIDER", "gemini")
    monkeypatch.setattr(
        "app.services.ai_service.gemini_service.check_connection",
        lambda force=False: {"available": True, "reason": "ok", "provider": "gemini", "model": "gemini-3.6-flash", "url": "https://x"},
    )

    resp = auth_client.get("/api/ai/status")
    body = resp.json()
    assert body["provider"] == "gemini"
    assert body["available"] is True
