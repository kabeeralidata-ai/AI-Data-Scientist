"""Tests for the rebuilt AI Chat feature: Gemini-only routing (no Ollama fallback),
per-reason error messages, project-aware context building, conversation memory/follow-up
threading, and dataset-specific behavior (churn vs. product returns). Real Gemini calls
are always mocked here (see conftest.py's forced GEMINI_API_KEY="" for the one test that
deliberately exercises the real unconfigured path) — live verification against the real
Gemini API was done manually and is reported separately, not re-derived in this suite.
"""

import io
import random

import pytest

from app.services import ai_service
from app.services.gemini_service import GeminiUnavailableError


def create_project(client, name="Chat Project", description=None):
    resp = client.post("/api/projects", json={"name": name, "description": description})
    assert resp.status_code == 201
    return resp.json()["id"]


def upload_csv(client, project_id, content, filename="data.csv"):
    resp = client.post(
        f"/api/projects/{project_id}/datasets/upload",
        files={"file": (filename, io.BytesIO(content.encode()), "text/csv")},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def mock_gemini(monkeypatch, capture=None):
    """Mocks ai_service.answer_question (the single chokepoint the chat endpoint calls)
    to return a canned answer while optionally capturing the context/question/history it
    was invoked with, for assertions on what reaches the AI."""

    def fake(context, question, history=None):
        if capture is not None:
            capture["context"] = context
            capture["question"] = question
            capture["history"] = history
        return f"mocked answer to: {question}", "gemini"

    monkeypatch.setattr("app.api.ai.ai_service.answer_question", fake)


def _churn_csv(n=150, seed=1):
    random.seed(seed)
    rows = ["customer_id,age,region,tenure_months,churn,signup_date"]
    for i in range(n):
        churn = 1 if i % 3 == 0 else 0
        rows.append(f"CUST-{i},{20 + i % 50},{'North' if i % 2 else 'South'},{1 + i % 48},{churn},2024-{(i%12)+1:02d}-01")
    return "\n".join(rows)


def _retail_returns_csv(n=150, seed=2):
    random.seed(seed)
    rows = ["order_id,category,price,quantity,returned"]
    for i in range(n):
        rows.append(f"ORD-{i},cat{i % 4},{round(random.uniform(10, 300), 2)},{random.randint(1, 5)},{i % 3 == 0}")
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Gemini-only routing (no Ollama in the chat path)
# ---------------------------------------------------------------------------


def test_chat_never_calls_ollama_even_when_ai_provider_is_ollama(auth_client, monkeypatch):
    """Chat must reach Gemini regardless of the general AI_PROVIDER setting (which still
    governs Insights/Reports) — and must never fall back to Ollama on failure."""
    monkeypatch.setattr("app.services.ai_service.settings.AI_PROVIDER", "ollama")

    ollama_called = {"value": False}

    def spy_ollama(context, question, history=None):
        ollama_called["value"] = True
        return "ollama answer"

    monkeypatch.setattr("app.services.ai_service.ollama_service.answer_question", spy_ollama)
    monkeypatch.setattr(
        "app.services.ai_service.gemini_service.answer_question",
        lambda context, question, history=None: "gemini answer",
    )

    project_id = create_project(auth_client)
    resp = auth_client.post("/api/ai/chat", json={"project_id": project_id, "message": "Hello"})
    assert resp.status_code == 200
    assert resp.json()["provider"] == "gemini"
    assert ollama_called["value"] is False


def test_chat_degrades_without_ollama_fallback_when_gemini_fails(auth_client, monkeypatch):
    ollama_called = {"value": False}

    def spy_ollama(context, question, history=None):
        ollama_called["value"] = True
        return "ollama fallback"

    def failing_gemini(context, question, history=None):
        raise GeminiUnavailableError("simulated", reason="rate_limited")

    monkeypatch.setattr("app.services.ai_service.gemini_service.answer_question", failing_gemini)
    monkeypatch.setattr("app.services.ai_service.ollama_service.answer_question", spy_ollama)

    project_id = create_project(auth_client)
    resp = auth_client.post("/api/ai/chat", json={"project_id": project_id, "message": "Hello"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ai_available"] is False
    assert ollama_called["value"] is False
    assert "rate limit" in body["answer"].lower()


# ---------------------------------------------------------------------------
# Per-reason error messages (Section 10) — never a bare "Something went wrong."
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason,expected_snippet",
    [
        ("not_configured", "isn't set up"),
        ("invalid_key", "authenticate"),
        ("rate_limited", "rate limit"),
        ("timeout", "timed out"),
        ("unreachable", "reach"),
        ("server_error", "server issue"),
        ("blocked", "declined"),
        ("empty_response", "empty response"),
    ],
)
def test_chat_shows_a_specific_message_per_failure_reason(auth_client, monkeypatch, reason, expected_snippet):
    def failing_gemini(context, question, history=None):
        raise GeminiUnavailableError("simulated", reason=reason)

    monkeypatch.setattr("app.services.ai_service.gemini_service.answer_question", failing_gemini)

    project_id = create_project(auth_client)
    resp = auth_client.post("/api/ai/chat", json={"project_id": project_id, "message": "Hello"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ai_available"] is False
    assert body["ai_generated"] is False
    assert expected_snippet in body["answer"].lower()
    # Never a stack trace or the literal exception repr.
    assert "traceback" not in body["answer"].lower()
    assert "GeminiUnavailableError" not in body["answer"]


def test_chat_error_message_never_contains_the_api_key(auth_client, monkeypatch):
    def failing_gemini(context, question, history=None):
        raise GeminiUnavailableError("simulated failure near key SECRET_KEY_VALUE", reason="invalid_key")

    monkeypatch.setattr("app.services.ai_service.gemini_service.answer_question", failing_gemini)
    project_id = create_project(auth_client)
    resp = auth_client.post("/api/ai/chat", json={"project_id": project_id, "message": "Hello"})
    assert "SECRET_KEY_VALUE" not in resp.json()["answer"]


def test_chat_real_unconfigured_path_end_to_end(auth_client):
    """No mocking at all — GEMINI_API_KEY is forced empty for the whole test suite (see
    conftest.py), so this exercises the actual missing-key code path for real."""
    project_id = create_project(auth_client)
    resp = auth_client.post("/api/ai/chat", json={"project_id": project_id, "message": "What is overfitting?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ai_available"] is False
    assert "gemini" in body["answer"].lower()


# ---------------------------------------------------------------------------
# Project-aware context (Section 4) — verifies each category reaches the AI when it
# genuinely exists, and is absent when it doesn't (never invented).
# ---------------------------------------------------------------------------


def test_context_includes_project_and_dataset_detail(auth_client, monkeypatch):
    capture = {}
    mock_gemini(monkeypatch, capture)
    project_id = create_project(auth_client, name="Churn Co", description="Predict customer churn.")
    dataset_id = upload_csv(auth_client, project_id, _churn_csv())
    auth_client.post(f"/api/analysis/{dataset_id}/clean", json={"missing_strategy": "auto", "remove_duplicates": True})

    auth_client.post("/api/ai/chat", json={"project_id": project_id, "message": "What is this about?"})

    ctx = capture["context"]
    assert ctx["project"]["name"] == "Churn Co"
    assert ctx["project"]["description"] == "Predict customer churn."
    assert ctx["dataset"]["file_name"] == "data.csv"
    assert ctx["dataset"]["rows"] == 150
    assert "customer_id" in [c["name"] for c in ctx["dataset"]["columns_detail"]]
    assert ctx["analysis_stage"] == "data_cleaned"


def test_context_includes_data_quality_original_and_cleaning_summary(auth_client, monkeypatch):
    capture = {}
    mock_gemini(monkeypatch, capture)
    project_id = create_project(auth_client)
    rows = ["age,income"]
    for i in range(50):
        rows.append(f"{20 + i},{'' if i % 10 == 0 else 30000 + i}")
    dataset_id = upload_csv(auth_client, project_id, "\n".join(rows))
    auth_client.post(f"/api/analysis/{dataset_id}/clean", json={"missing_strategy": "auto", "remove_duplicates": True})

    auth_client.post("/api/ai/chat", json={"project_id": project_id, "message": "data quality?"})

    quality = capture["context"]["data_quality"]
    assert "missing_values" in quality["original_issues_by_type"]
    assert quality["original_issues_by_type"]["missing_values"]["count"] >= 1
    assert quality["cleaning_performed"] is not None


def test_context_includes_model_features_used_and_excluded(auth_client, monkeypatch):
    capture = {}
    project_id = create_project(auth_client)
    dataset_id = upload_csv(auth_client, project_id, _churn_csv())
    train_resp = auth_client.post(
        "/api/models/train",
        json={"dataset_id": dataset_id, "target_column": "churn", "models": ["logistic_regression"]},
    )
    assert train_resp.status_code == 201

    mock_gemini(monkeypatch, capture)
    auth_client.post("/api/ai/chat", json={"project_id": project_id, "message": "explain the model"})

    model_ctx = capture["context"]["model"]
    assert model_ctx["target"] == "churn" if "target" in model_ctx else capture["context"]["target"] == "churn"
    assert len(model_ctx["features_used"]) > 0
    # Human-readable, not raw encoded feature names.
    assert not any(f.startswith("contract_type_") for f in model_ctx["features_used"])
    assert "id_like_columns" in model_ctx["features_excluded"]
    assert any("customer" in c.lower() or "id" in c.lower() for c in model_ctx["features_excluded"]["id_like_columns"])


def test_context_omits_sections_that_do_not_exist(auth_client, monkeypatch):
    """A brand-new project with no dataset/model/report must not have those keys at all
    — 'only include information that actually exists', never an invented placeholder."""
    capture = {}
    mock_gemini(monkeypatch, capture)
    project_id = create_project(auth_client, name="Empty Project")
    auth_client.post("/api/ai/chat", json={"project_id": project_id, "message": "hello"})

    ctx = capture["context"]
    assert "dataset" not in ctx
    assert "model" not in ctx
    assert "latest_report" not in ctx
    assert ctx["analysis_stage"] == "no_dataset_uploaded"


# ---------------------------------------------------------------------------
# Conversation memory / follow-up (Section 8)
# ---------------------------------------------------------------------------


def test_follow_up_question_includes_prior_history(auth_client, monkeypatch):
    capture = {}
    mock_gemini(monkeypatch, capture)
    project_id = create_project(auth_client)

    first = auth_client.post("/api/ai/chat", json={"project_id": project_id, "message": "What is the target?"})
    assert first.status_code == 200
    conversation_id = first.json()["conversation_id"]

    second = auth_client.post(
        "/api/ai/chat",
        json={"project_id": project_id, "message": "Why is it difficult to predict?", "conversation_id": conversation_id},
    )
    assert second.status_code == 200
    assert second.json()["conversation_id"] == conversation_id

    history = capture["history"]
    assert any(m["content"] == "What is the target?" for m in history)
    assert any("mocked answer" in m["content"] for m in history)


def test_conversation_persists_across_requests(auth_client, monkeypatch):
    mock_gemini(monkeypatch)
    project_id = create_project(auth_client)
    resp = auth_client.post("/api/ai/chat", json={"project_id": project_id, "message": "hi"})
    conversation_id = resp.json()["conversation_id"]

    conversations = auth_client.get(f"/api/ai/conversations/{project_id}").json()
    assert len(conversations) == 1
    assert conversations[0]["id"] == conversation_id
    assert len(conversations[0]["messages"]) == 2
    assert conversations[0]["messages"][0]["role"] == "user"
    assert conversations[0]["messages"][1]["role"] == "assistant"


def test_invalid_conversation_id_returns_404(auth_client):
    import uuid

    project_id = create_project(auth_client)
    resp = auth_client.post(
        "/api/ai/chat",
        json={"project_id": project_id, "message": "hi", "conversation_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Dataset-specific behavior (Section 13): customer identity / churn vs. returns
# ---------------------------------------------------------------------------


def test_dataset_identity_flags_customer_id_and_churn_target(auth_client, monkeypatch):
    capture = {}
    project_id = create_project(auth_client)
    dataset_id = upload_csv(auth_client, project_id, _churn_csv())
    auth_client.post(
        "/api/models/train", json={"dataset_id": dataset_id, "target_column": "churn", "models": ["logistic_regression"]}
    )
    mock_gemini(monkeypatch, capture)
    auth_client.post("/api/ai/chat", json={"project_id": project_id, "message": "who will churn?"})

    identity = capture["context"]["dataset_identity"]
    assert identity["has_customer_identifier"] is True
    assert identity["customer_identifier_column"] == "customer_id"
    assert identity["target_is_churn_like"] is True
    assert "guidance" not in identity


def test_dataset_identity_flags_missing_customer_id_for_churn_like_target(auth_client, monkeypatch):
    """A target literally named 'churn' but with no customer identifier must be flagged
    so the AI can honestly say individual-level prediction isn't possible."""
    capture = {}
    project_id = create_project(auth_client)
    rows = ["order_id,amount,churn"]
    random.seed(3)
    for i in range(100):
        rows.append(f"ORD-{i},{round(random.uniform(10, 200), 2)},{i % 2}")
    dataset_id = upload_csv(auth_client, project_id, "\n".join(rows))
    auth_client.post(
        "/api/models/train", json={"dataset_id": dataset_id, "target_column": "churn", "models": ["logistic_regression"]}
    )
    mock_gemini(monkeypatch, capture)
    auth_client.post("/api/ai/chat", json={"project_id": project_id, "message": "who will churn?"})

    identity = capture["context"]["dataset_identity"]
    assert identity["has_customer_identifier"] is False
    assert identity["target_is_churn_like"] is True
    assert "guidance" in identity
    assert "customer" in identity["guidance"].lower()


def test_dataset_identity_does_not_flag_a_non_churn_target(auth_client, monkeypatch):
    """A retail 'returned' target must not be treated as churn-like."""
    capture = {}
    project_id = create_project(auth_client)
    dataset_id = upload_csv(auth_client, project_id, _retail_returns_csv())
    auth_client.post(
        "/api/models/train", json={"dataset_id": dataset_id, "target_column": "returned", "models": ["logistic_regression"]}
    )
    mock_gemini(monkeypatch, capture)
    auth_client.post("/api/ai/chat", json={"project_id": project_id, "message": "which orders are returned?"})

    identity = capture["context"]["dataset_identity"]
    assert identity["target_is_churn_like"] is False
