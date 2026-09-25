import io

import httpx
import pytest

from app.services.dataset_service import find_ungrounded_concepts
from app.services.ollama_service import OllamaService, OllamaUnavailableError


def test_is_available_returns_false_when_server_unreachable():
    service = OllamaService()
    service.base_url = "http://localhost:1"  # invalid/unreachable port
    assert service.is_available() is False


def test_generate_insight_raises_when_unreachable():
    service = OllamaService()
    service.base_url = "http://localhost:1"
    service.timeout = 1
    with pytest.raises(OllamaUnavailableError):
        service.generate_insight({"foo": "bar"})


def test_ai_insights_endpoint_degrades_gracefully_when_ai_down(auth_client):
    project_resp = auth_client.post("/api/projects", json={"name": "AI Project"})
    project_id = project_resp.json()["id"]

    import io

    content = "age,income,churn\n25,50000,0\n40,60000,1\n35,55000,0\n" * 10
    upload_resp = auth_client.post(
        f"/api/projects/{project_id}/datasets/upload",
        files={"file": ("data.csv", io.BytesIO(content.encode()), "text/csv")},
    )
    dataset_id = upload_resp.json()["id"]

    resp = auth_client.post("/api/ai/insights", json={"dataset_id": dataset_id})
    assert resp.status_code == 200
    body = resp.json()
    assert "insight" in body
    assert isinstance(body["ai_available"], bool)


def test_ai_status_endpoint_never_raises(client):
    resp = client.get("/api/ai/status")
    assert resp.status_code == 200
    assert "available" in resp.json()


def test_check_connection_reports_unreachable_reason():
    service = OllamaService()
    service.base_url = "http://localhost:1"
    result = service.check_connection()
    assert result["available"] is False
    assert result["reason"] == "unreachable"
    assert "localhost:1" in result["detail"] or "http://localhost:1" in result["detail"]


def test_check_connection_reports_timeout_reason():
    service = OllamaService()
    service.base_url = "http://10.255.255.1"  # non-routable, should time out rather than refuse
    result = service.check_connection()
    assert result["available"] is False
    assert result["reason"] in ("timeout", "unreachable")


def test_ai_status_endpoint_exposes_url_and_model(client):
    resp = client.get("/api/ai/status")
    body = resp.json()
    assert body["url"]
    assert body["model"]
    assert "reason" in body


def test_find_ungrounded_concepts_flags_words_with_no_matching_column():
    columns = ["store_city", "sales_amount", "unit_price"]
    ungrounded = find_ungrounded_concepts("Which segment has the highest churn?", columns, target_column="sales_amount")
    assert "churn" in ungrounded
    assert "segment" in ungrounded


def test_find_ungrounded_concepts_ignores_words_that_do_match_a_real_column():
    columns = ["profit", "sales_amount", "unit_price"]
    ungrounded = find_ungrounded_concepts("What drives profit the most?", columns, target_column="sales_amount")
    assert ungrounded == []


def test_chat_flags_nonexistent_dataset_concepts_as_a_hint_not_a_hard_block(auth_client, monkeypatch):
    """A question mentioning a concept with no matching column (e.g. 'churn' in a
    retail-sales project) must no longer be answered by a hard-coded, AI-bypassing
    canned response — general/definitional questions sharing a vocabulary word (e.g.
    'what is churn?') need to still reach the AI. Instead, the mismatch is now passed to
    the AI as an advisory hint in the context, and the AI itself decides whether the
    question is asking about THIS dataset (and should be told honestly) or is general.
    This verifies the hint is computed and passed correctly; the AI's own judgment on
    that hint is verified separately via live testing, not something an offline test can
    check."""
    project_resp = auth_client.post("/api/projects", json={"name": "Retail Chat Project"})
    project_id = project_resp.json()["id"]

    content = "store_city,sales_amount,unit_price\nKarachi,120.5,15.0\nLahore,90.0,10.0\n" * 10
    upload_resp = auth_client.post(
        f"/api/projects/{project_id}/datasets/upload",
        files={"file": ("retail.csv", io.BytesIO(content.encode()), "text/csv")},
    )
    assert upload_resp.status_code == 201

    captured_context = {}

    def fake_answer_question(context, question, history=None):
        captured_context.update(context)
        return "mocked answer", "gemini"

    monkeypatch.setattr("app.api.ai.ai_service.answer_question", fake_answer_question)

    chat_resp = auth_client.post(
        "/api/ai/chat",
        json={"project_id": project_id, "message": "Which segment has the highest churn?"},
    )
    assert chat_resp.status_code == 200
    body = chat_resp.json()
    assert body["ai_generated"] is True
    assert body["answer"] == "mocked answer"

    hint = captured_context["question_terms_not_found_in_dataset"]
    assert "churn" in hint["terms"]
    assert "segment" in hint["terms"]
    assert "sales_amount" in hint["available_columns"] or "store_city" in hint["available_columns"]


def test_chat_degrades_gracefully_when_gemini_is_not_configured(auth_client):
    """The test environment always runs with an empty GEMINI_API_KEY (see conftest.py) —
    this exercises the REAL 'AI chat is unavailable' path end-to-end with no mocking,
    satisfying 'test without Gemini' (missing API key case) organically."""
    project_resp = auth_client.post("/api/projects", json={"name": "No Gemini Project"})
    project_id = project_resp.json()["id"]

    chat_resp = auth_client.post("/api/ai/chat", json={"project_id": project_id, "message": "What is overfitting?"})
    assert chat_resp.status_code == 200
    body = chat_resp.json()
    assert body["ai_available"] is False
    assert body["ai_generated"] is False
    assert body["provider"] is None
    # A clear, specific, non-technical message — never a raw stack trace or the API key.
    assert "gemini" in body["answer"].lower()
    assert "api key" in body["answer"].lower() or "not" in body["answer"].lower()
