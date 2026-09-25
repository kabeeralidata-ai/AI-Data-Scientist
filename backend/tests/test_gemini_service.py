"""Unit tests for GeminiService (Google AI Studio REST client) and the ai_service
provider-orchestration layer (Gemini primary + automatic Ollama fallback). No real network
calls are made — httpx.Client.post is monkeypatched to simulate each response shape."""

import httpx
import pytest

from app.services import ai_service
from app.services.gemini_service import GeminiService, GeminiUnavailableError


class FakeResponse:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


def _gemini_success_payload(text="This is the generated insight."):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def _service_with_key(key="test-key-123"):
    service = GeminiService()
    service.api_key = key
    service.timeout = 5
    return service


def test_check_connection_reports_not_configured_without_a_key():
    service = GeminiService()
    service.api_key = ""
    result = service.check_connection()
    assert result["available"] is False
    assert result["reason"] == "not_configured"
    assert result["provider"] == "gemini"


def test_generate_insight_succeeds_and_extracts_text(monkeypatch):
    service = _service_with_key()

    def fake_post(self, url, params=None, json=None):
        return FakeResponse(200, _gemini_success_payload("Revenue grew 12% this quarter."))

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    result = service.generate_insight({"foo": "bar"})
    assert result == "Revenue grew 12% this quarter."


def test_raises_invalid_key_on_401(monkeypatch):
    service = _service_with_key()
    monkeypatch.setattr(httpx.Client, "post", lambda self, url, params=None, json=None: FakeResponse(401, {}))
    with pytest.raises(GeminiUnavailableError) as exc_info:
        service.generate_insight({"foo": "bar"})
    assert exc_info.value.reason == "invalid_key"


def test_raises_invalid_key_on_403(monkeypatch):
    service = _service_with_key()
    monkeypatch.setattr(httpx.Client, "post", lambda self, url, params=None, json=None: FakeResponse(403, {}))
    with pytest.raises(GeminiUnavailableError) as exc_info:
        service.generate_insight({"foo": "bar"})
    assert exc_info.value.reason == "invalid_key"


def test_raises_invalid_key_on_the_real_google_400_shape(monkeypatch):
    """Regression test: Google's actual API does NOT return 401/403 for a bad key — it
    returns HTTP 400 INVALID_ARGUMENT with an ErrorInfo.reason of API_KEY_INVALID
    (confirmed against the live API). Before this fix, that fell through to a vague
    'Gemini returned status 400' instead of a clear invalid-key message."""
    service = _service_with_key()
    real_google_error_body = {
        "error": {
            "code": 400,
            "message": "API key not valid. Please pass a valid API key.",
            "status": "INVALID_ARGUMENT",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "API_KEY_INVALID",
                    "domain": "googleapis.com",
                }
            ],
        }
    }
    monkeypatch.setattr(httpx.Client, "post", lambda self, url, params=None, json=None: FakeResponse(400, real_google_error_body))
    with pytest.raises(GeminiUnavailableError) as exc_info:
        service.generate_insight({"foo": "bar"})
    assert exc_info.value.reason == "invalid_key"
    assert "API_KEY_INVALID" not in str(exc_info.value)  # message is user-friendly, not a raw error dump


def test_400_that_is_not_an_invalid_key_error_gets_a_generic_reason(monkeypatch):
    """A 400 for some OTHER reason (malformed request body, etc.) must not be
    misclassified as invalid_key — only the specific API_KEY_INVALID shape counts."""
    service = _service_with_key()
    other_400_body = {"error": {"code": 400, "message": "Invalid JSON payload.", "status": "INVALID_ARGUMENT", "details": []}}
    monkeypatch.setattr(httpx.Client, "post", lambda self, url, params=None, json=None: FakeResponse(400, other_400_body))
    with pytest.raises(GeminiUnavailableError) as exc_info:
        service.generate_insight({"foo": "bar"})
    assert exc_info.value.reason == "error"


def test_retries_on_429_then_succeeds(monkeypatch):
    service = _service_with_key()
    calls = {"count": 0}

    def fake_post(self, url, params=None, json=None):
        calls["count"] += 1
        if calls["count"] < 3:
            return FakeResponse(429, {})
        return FakeResponse(200, _gemini_success_payload("Recovered after retry."))

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    monkeypatch.setattr("app.services.gemini_service.time.sleep", lambda seconds: None)

    result = service.generate_insight({"foo": "bar"})
    assert result == "Recovered after retry."
    assert calls["count"] == 3


def test_raises_rate_limited_after_exhausting_retries(monkeypatch):
    service = _service_with_key()
    monkeypatch.setattr(httpx.Client, "post", lambda self, url, params=None, json=None: FakeResponse(429, {}))
    monkeypatch.setattr("app.services.gemini_service.time.sleep", lambda seconds: None)

    with pytest.raises(GeminiUnavailableError) as exc_info:
        service.generate_insight({"foo": "bar"})
    assert exc_info.value.reason == "rate_limited"


def test_raises_unreachable_on_connect_error(monkeypatch):
    service = _service_with_key()

    def raise_connect_error(self, url, params=None, json=None):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.Client, "post", raise_connect_error)
    with pytest.raises(GeminiUnavailableError) as exc_info:
        service.generate_insight({"foo": "bar"})
    assert exc_info.value.reason == "unreachable"


def test_raises_timeout_on_timeout_exception(monkeypatch):
    service = _service_with_key()

    def raise_timeout(self, url, params=None, json=None):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx.Client, "post", raise_timeout)
    with pytest.raises(GeminiUnavailableError) as exc_info:
        service.generate_insight({"foo": "bar"})
    assert exc_info.value.reason == "timeout"


def test_raises_empty_response_when_no_candidates(monkeypatch):
    service = _service_with_key()
    monkeypatch.setattr(httpx.Client, "post", lambda self, url, params=None, json=None: FakeResponse(200, {"candidates": []}))
    with pytest.raises(GeminiUnavailableError) as exc_info:
        service.generate_insight({"foo": "bar"})
    assert exc_info.value.reason == "empty_response"


def test_reports_blocked_reason_when_prompt_feedback_blocks(monkeypatch):
    service = _service_with_key()
    monkeypatch.setattr(
        httpx.Client,
        "post",
        lambda self, url, params=None, json=None: FakeResponse(200, {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}),
    )
    with pytest.raises(GeminiUnavailableError) as exc_info:
        service.generate_insight({"foo": "bar"})
    assert exc_info.value.reason == "blocked"


def test_api_key_is_never_logged(monkeypatch, caplog):
    service = _service_with_key(key="super-secret-key-do-not-log")
    monkeypatch.setattr(httpx.Client, "post", lambda self, url, params=None, json=None: FakeResponse(401, {}))
    with caplog.at_level("DEBUG"):
        with pytest.raises(GeminiUnavailableError):
            service.generate_insight({"foo": "bar"})
    assert "super-secret-key-do-not-log" not in caplog.text


def test_httpx_request_logging_is_suppressed_so_the_gemini_key_never_reaches_the_log():
    """Regression test: httpx logs each outgoing request's full URL at INFO level by
    default, and Gemini's REST API takes the API key as a URL query parameter
    (?key=...). Left at its default level, httpx's OWN logging (not gemini_service's,
    which never logs the key) silently wrote the raw key to the server log on every
    single Gemini request — confirmed via a live manual test against the real API.
    app/main.py must suppress the httpx/httpcore loggers below INFO on startup."""
    import logging

    import app.main  # noqa: F401 (importing triggers the module-level logging setup)

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_answer_question_includes_history_and_returns_text(monkeypatch):
    service = _service_with_key()
    monkeypatch.setattr(
        httpx.Client, "post", lambda self, url, params=None, json=None: FakeResponse(200, _gemini_success_payload("Answer text."))
    )
    result = service.answer_question({"foo": "bar"}, "What drove the increase?", history=[{"role": "user", "content": "hi"}])
    assert result == "Answer text."


# ---- ai_service (provider orchestration) ----


def test_ai_service_uses_ollama_only_when_provider_is_ollama(monkeypatch):
    monkeypatch.setattr("app.services.ai_service.settings.AI_PROVIDER", "ollama")

    def fake_ollama_insight(context):
        return "ollama insight"

    monkeypatch.setattr("app.services.ai_service.ollama_service.generate_insight", fake_ollama_insight)
    insight, provider = ai_service.generate_insight({"foo": "bar"})
    assert insight == "ollama insight"
    assert provider == "ollama"


def test_ai_service_uses_gemini_when_provider_is_gemini_and_gemini_succeeds(monkeypatch):
    monkeypatch.setattr("app.services.ai_service.settings.AI_PROVIDER", "gemini")
    monkeypatch.setattr("app.services.ai_service.gemini_service.generate_insight", lambda context: "gemini insight")
    insight, provider = ai_service.generate_insight({"foo": "bar"})
    assert insight == "gemini insight"
    assert provider == "gemini"


def test_ai_service_falls_back_to_ollama_when_gemini_fails(monkeypatch):
    monkeypatch.setattr("app.services.ai_service.settings.AI_PROVIDER", "gemini")

    def failing_gemini(context):
        raise GeminiUnavailableError("simulated failure", reason="invalid_key")

    monkeypatch.setattr("app.services.ai_service.gemini_service.generate_insight", failing_gemini)
    monkeypatch.setattr("app.services.ai_service.ollama_service.generate_insight", lambda context: "ollama fallback insight")

    insight, provider = ai_service.generate_insight({"foo": "bar"})
    assert insight == "ollama fallback insight"
    assert provider == "ollama"


def test_ai_service_raises_when_both_gemini_and_ollama_fail(monkeypatch):
    from app.services.ollama_service import OllamaUnavailableError

    monkeypatch.setattr("app.services.ai_service.settings.AI_PROVIDER", "gemini")

    def failing_gemini(context):
        raise GeminiUnavailableError("gemini down", reason="unreachable")

    def failing_ollama(context):
        raise OllamaUnavailableError("ollama down")

    monkeypatch.setattr("app.services.ai_service.gemini_service.generate_insight", failing_gemini)
    monkeypatch.setattr("app.services.ai_service.ollama_service.generate_insight", failing_ollama)

    with pytest.raises(ai_service.AIUnavailableError):
        ai_service.generate_insight({"foo": "bar"})


def test_ai_service_answer_question_is_gemini_only_and_never_falls_back_to_ollama(monkeypatch):
    """AI Chat is deliberately Gemini-only: a wrong/lower-quality answer silently produced
    by a swapped-in fallback model would be worse than a clear 'unavailable' message. This
    holds regardless of AI_PROVIDER (chat never uses Ollama, unlike Insights/Reports)."""
    monkeypatch.setattr("app.services.ai_service.settings.AI_PROVIDER", "gemini")

    def failing_gemini(context, question, history=None):
        raise GeminiUnavailableError("simulated failure", reason="rate_limited")

    monkeypatch.setattr("app.services.ai_service.gemini_service.answer_question", failing_gemini)

    ollama_called = {"value": False}

    def spy_ollama(context, question, history=None):
        ollama_called["value"] = True
        return "ollama fallback answer"

    monkeypatch.setattr("app.services.ai_service.ollama_service.answer_question", spy_ollama)

    with pytest.raises(ai_service.AIUnavailableError) as exc_info:
        ai_service.answer_question({"foo": "bar"}, "question?")

    assert ollama_called["value"] is False
    # The original Gemini failure reason is preserved so the API layer can show a
    # specific, useful message (e.g. "rate limited") instead of a generic failure.
    assert exc_info.value.reason == "rate_limited"


def test_ai_service_answer_question_succeeds_with_gemini(monkeypatch):
    monkeypatch.setattr("app.services.ai_service.gemini_service.answer_question", lambda context, question, history=None: "gemini answer")
    answer, provider = ai_service.answer_question({"foo": "bar"}, "question?")
    assert answer == "gemini answer"
    assert provider == "gemini"


def test_check_status_reports_fallback_block_when_gemini_primary(monkeypatch):
    monkeypatch.setattr("app.services.ai_service.settings.AI_PROVIDER", "gemini")
    monkeypatch.setattr(
        "app.services.ai_service.gemini_service.check_connection",
        lambda force=False: {"available": False, "reason": "not_configured", "provider": "gemini", "model": "gemini-2.5-flash"},
    )
    monkeypatch.setattr(
        "app.services.ai_service.ollama_service.check_connection",
        lambda: {"available": False, "reason": "unreachable", "model": "qwen2.5:3b"},
    )

    status = ai_service.check_status()
    assert status["provider"] == "gemini"
    assert status["available"] is False
    assert "fallback" in status
    assert status["fallback"]["provider"] == "ollama"
