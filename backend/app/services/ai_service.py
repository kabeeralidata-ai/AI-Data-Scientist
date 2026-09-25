"""Single entry point for AI generation used by the whole app (AI Insights, AI Chat,
Auto Analyze's AI step). generate_insight/generate_report_insight pick the configured
provider (AI_PROVIDER=gemini|ollama) and, when Gemini is primary, fall back to Ollama if
Gemini fails. answer_question (AI Chat) is the one exception: it is Gemini-only and never
falls back to Ollama/Qwen/any other provider — see its docstring."""

import logging

from app.core.config import settings
from app.services.gemini_service import GeminiUnavailableError, gemini_service
from app.services.ollama_service import OllamaUnavailableError, ollama_service

logger = logging.getLogger("ai_service")


class AIUnavailableError(Exception):
    def __init__(self, message: str, reason: str = "error"):
        super().__init__(message)
        self.reason = reason


# Maps AIUnavailableError.reason (propagated from GeminiUnavailableError's classification
# of the actual HTTP/network failure — see gemini_service.py) to a clear, specific,
# non-technical message, so the user never sees a generic "unavailable"/"unreachable"
# regardless of the real cause. Never includes the API key or a stack trace. Shared by
# every AI surface (Chat, Insights, Auto Analyze's AI step) so a rate limit, an invalid
# key, or a missing model reads the same way everywhere in the app — not just in the two
# places that happened to check it first.
UNAVAILABLE_MESSAGES = {
    "not_configured": "isn't set up yet — the Gemini API key is missing on the server. Please contact your administrator.",
    "invalid_key": "couldn't authenticate with Gemini (the configured API key is invalid or expired). Please contact your administrator.",
    "rate_limited": "has hit Gemini's rate limit or daily quota. Please wait a moment (or try again tomorrow if the daily quota is exhausted).",
    "timeout": "timed out waiting for a response from Gemini. Please try again.",
    "unreachable": "couldn't reach Gemini right now (network issue). Please try again shortly.",
    "server_error": "Gemini is having a temporary server issue. Please try again shortly.",
    "model_not_found": "'s configured Gemini model was not found. Please contact your administrator.",
    "blocked": "Gemini declined to respond to that request.",
    "empty_response": "Gemini returned an empty response. Please try again.",
}


def unavailable_message(surface: str, reason: str) -> str:
    """`surface` is a short label ('AI chat', 'AI Insights generation') prefixed onto the
    reason-specific clause, so the same reason table reads naturally for every caller."""
    clause = UNAVAILABLE_MESSAGES.get(reason)
    if clause:
        return f"{surface} {clause}"
    return f"{surface} is temporarily unavailable. Your project's analytical results remain available in the dashboard."


def _use_gemini_first() -> bool:
    return settings.AI_PROVIDER.strip().lower() == "gemini"


def generate_insight(context: dict) -> tuple[str, str]:
    """Returns (insight_text, provider_used). Raises AIUnavailableError only if every
    configured provider failed."""
    if _use_gemini_first():
        try:
            return gemini_service.generate_insight(context), "gemini"
        except GeminiUnavailableError as exc:
            logger.info("Gemini unavailable (%s), falling back to Ollama.", exc.reason)
            try:
                return ollama_service.generate_insight(context), "ollama"
            except OllamaUnavailableError as fallback_exc:
                # Preserve Gemini's own classified reason (e.g. rate_limited,
                # invalid_key, model_not_found) so the API layer can still show a
                # specific message — the fallback also failing shouldn't erase why
                # the PRIMARY provider failed, which is almost always the more
                # actionable piece of information.
                raise AIUnavailableError(f"gemini: {exc}; ollama fallback: {fallback_exc}", reason=exc.reason) from fallback_exc

    try:
        return ollama_service.generate_insight(context), "ollama"
    except OllamaUnavailableError as exc:
        raise AIUnavailableError(str(exc)) from exc


def generate_report_insight(context: dict) -> tuple[str, str]:
    """Same Gemini-first/Ollama-fallback routing as generate_insight, but using the
    report-specific structured-sections prompt. Used exclusively by report_service so the
    'AI Business Insights' report section always goes through the same shared provider
    configuration as the rest of the app, never a separate/bespoke AI call."""
    if _use_gemini_first():
        try:
            return gemini_service.generate_report_insight(context), "gemini"
        except GeminiUnavailableError as exc:
            logger.info("Gemini unavailable (%s), falling back to Ollama.", exc.reason)
            try:
                return ollama_service.generate_report_insight(context), "ollama"
            except OllamaUnavailableError as fallback_exc:
                raise AIUnavailableError(f"gemini: {exc}; ollama fallback: {fallback_exc}", reason=exc.reason) from fallback_exc

    try:
        return ollama_service.generate_report_insight(context), "ollama"
    except OllamaUnavailableError as exc:
        raise AIUnavailableError(str(exc)) from exc


def answer_question(context: dict, question: str, history: list[dict] | None = None) -> tuple[str, str]:
    """AI Chat is Gemini-only by design — deliberately does NOT fall back to Ollama/Qwen/any
    other provider. A chat answer silently produced by a different, unannounced model would
    be more confusing than a clear 'AI chat is unavailable right now' message naming exactly
    what failed (missing key, rate limit, timeout, ...) via the raised error's `.reason`."""
    try:
        return gemini_service.answer_question(context, question, history), "gemini"
    except GeminiUnavailableError as exc:
        logger.info("Gemini unavailable for chat (%s) — no fallback provider is used for chat.", exc.reason)
        raise AIUnavailableError(str(exc), reason=exc.reason) from exc


def check_status(force: bool = False) -> dict:
    """Status for the Settings 'AI Status' card / navbar dot. Reports the active
    (primary) provider at the top level for backward compatibility, plus a nested
    `fallback` status when Gemini is primary (Ollama is always the fallback then).
    force=True (the explicit 'Test Connection' button) always spends a real Gemini
    request; force=False (passive/background polling) reuses gemini_service's own
    short-lived cache so it doesn't quietly burn quota in the background."""
    if _use_gemini_first():
        primary = gemini_service.check_connection(force=force)
        fallback = ollama_service.check_connection()
        fallback["provider"] = "ollama"
        return {
            **primary,
            "available": primary["available"] or fallback["available"],
            "fallback": fallback,
        }

    status = ollama_service.check_connection()
    status["provider"] = "ollama"
    return status
