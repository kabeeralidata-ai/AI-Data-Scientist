import json
import logging
import time

import httpx

from app.core.config import settings
from app.services.ai_prompts import SYSTEM_PROMPT

logger = logging.getLogger("gemini_service")

GEMINI_MAX_RETRIES = 3
GEMINI_BACKOFF_BASE_SECONDS = 1.0

# check_connection() makes a REAL Gemini request to verify reachability — necessary for
# an honest "Test Connection" result, but expensive if it fires on every passive status
# poll (the navbar dot and AITab both mount useAIStatus(), which refetches every 60s).
# A short cache means background polling reuses the last real result almost all the
# time, while an explicit "Test Connection" click (force=True) always gets a fresh one.
_CONNECTION_CHECK_CACHE: dict[str, tuple[float, dict]] = {}
_CONNECTION_CHECK_TTL_SECONDS = 300


class GeminiUnavailableError(Exception):
    def __init__(self, message: str, reason: str = "error"):
        super().__init__(message)
        self.reason = reason


def _is_invalid_key_error(resp: httpx.Response) -> bool:
    """Google's REST API reports a bad key as HTTP 400 INVALID_ARGUMENT with an
    ErrorInfo.reason of API_KEY_INVALID — never logs the key itself, only inspects the
    (key-free) error body Google sends back."""
    try:
        data = resp.json()
    except Exception:
        return False
    error = data.get("error") or {}
    if error.get("status") == "INVALID_ARGUMENT":
        for detail in error.get("details", []):
            if detail.get("reason") == "API_KEY_INVALID":
                return True
    return False


def _google_error_status(resp: httpx.Response) -> str | None:
    """The Google RPC status enum (e.g. RESOURCE_EXHAUSTED, INVALID_ARGUMENT) from the
    error body, if present — never the key, never the full payload."""
    try:
        return (resp.json().get("error") or {}).get("status")
    except Exception:
        return None


def _log_gemini_failure(
    purpose: str, model: str, status_code: int | str | None, reason: str, message: str, google_status: str | None = None
) -> None:
    """One structured, greppable line per failure — provider/model/purpose/status/reason
    always present, the API key and full request/response payload NEVER included."""
    logger.warning(
        "Gemini request failed: provider=gemini model=%s purpose=%s status=%s google_status=%s reason=%s message=%s",
        model,
        purpose,
        status_code,
        google_status or "-",
        reason,
        message,
    )


class GeminiService:
    def __init__(self):
        self.api_key = settings.GEMINI_API_KEY
        self.model = settings.GEMINI_MODEL
        self.timeout = settings.GEMINI_TIMEOUT_SECONDS
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def check_connection(self, force: bool = False) -> dict:
        """Diagnoses exactly why Gemini is unreachable (not configured, invalid key, rate
        limited, network-unreachable, timeout) for the Settings 'AI Status' card and the
        navbar indicator. Cached briefly (see _CONNECTION_CHECK_TTL_SECONDS) so frequent
        passive/background polling doesn't repeatedly spend real Gemini quota just to
        show a status dot — pass force=True (the explicit 'Test Connection' button) to
        always get a fresh live result."""
        base = {"model": self.model, "provider": "gemini", "url": self.base_url}
        if not self.is_configured():
            return {
                **base,
                "available": False,
                "reason": "not_configured",
                "detail": "GEMINI_API_KEY is not set. Add it to backend/.env to enable Gemini.",
            }

        cache_key = f"{self.model}:{self.api_key[-6:] if len(self.api_key) >= 6 else self.api_key}"
        if not force:
            cached = _CONNECTION_CHECK_CACHE.get(cache_key)
            if cached and (time.time() - cached[0]) < _CONNECTION_CHECK_TTL_SECONDS:
                return {**cached[1], "cached": True}

        try:
            self._generate(
                "Reply with exactly the word: OK",
                system_prompt="Follow the instruction exactly.",
                timeout=10,
                max_retries=1,
                purpose="connection_test",
            )
            result = {**base, "available": True, "reason": "ok", "detail": "Gemini is reachable and the configured model responded successfully."}
        except GeminiUnavailableError as exc:
            result = {**base, "available": False, "reason": exc.reason, "detail": str(exc)}

        _CONNECTION_CHECK_CACHE[cache_key] = (time.time(), result)
        return {**result, "cached": False}

    def _generate(
        self,
        user_prompt: str,
        system_prompt: str,
        timeout: float | None = None,
        max_retries: int = GEMINI_MAX_RETRIES,
        purpose: str = "generate",
    ) -> str:
        url = f"{self.base_url}/models/{self.model}:generateContent"
        # The API key is a query param per Google's documented REST auth — never logged
        # below (only status codes / generic error types are logged, never the URL/params).
        params = {"key": self.api_key}
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        }

        backoff = GEMINI_BACKOFF_BASE_SECONDS
        for attempt in range(max_retries):
            try:
                with httpx.Client(timeout=timeout or self.timeout) as client:
                    resp = client.post(url, params=params, json=payload)
            except httpx.TimeoutException as exc:
                _log_gemini_failure(purpose, self.model, "timeout", "timeout", "Request exceeded the configured timeout.")
                raise GeminiUnavailableError("The Gemini request timed out.", reason="timeout") from exc
            except httpx.ConnectError as exc:
                _log_gemini_failure(purpose, self.model, "connect_error", "unreachable", "Could not establish a network connection.")
                raise GeminiUnavailableError("Could not connect to Gemini (network unreachable).", reason="unreachable") from exc
            except httpx.HTTPError as exc:
                _log_gemini_failure(purpose, self.model, "http_error", "error", type(exc).__name__)
                raise GeminiUnavailableError(f"Gemini request failed: {type(exc).__name__}.", reason="error") from exc

            if resp.status_code in (401, 403) or (resp.status_code == 400 and _is_invalid_key_error(resp)):
                # Google's REST API returns 400 INVALID_ARGUMENT (reason API_KEY_INVALID)
                # for a malformed/invalid key, not 401/403 as HTTP convention might
                # suggest — both are treated as the same "bad key" case here so the error
                # is always clear rather than falling through to a vague "status 400".
                _log_gemini_failure(
                    purpose, self.model, resp.status_code, "invalid_key", "API key rejected.", _google_error_status(resp)
                )
                raise GeminiUnavailableError(
                    "Gemini rejected the API key (invalid, expired, or unauthorized for this model). "
                    "Check GEMINI_API_KEY in backend/.env.",
                    reason="invalid_key",
                )

            if resp.status_code == 429:
                google_status = _google_error_status(resp)
                logger.info(
                    "Gemini rate limit hit: provider=gemini model=%s purpose=%s attempt=%s/%s google_status=%s",
                    self.model,
                    purpose,
                    attempt + 1,
                    max_retries,
                    google_status or "-",
                )
                if attempt < max_retries - 1:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                _log_gemini_failure(
                    purpose, self.model, 429, "rate_limited", "Quota/rate limit exhausted after retries.", google_status
                )
                raise GeminiUnavailableError(
                    "Gemini rate limit exceeded — too many requests. Try again shortly.",
                    reason="rate_limited",
                )

            if resp.status_code == 404:
                # Google returns 404 when the configured model name doesn't exist or isn't
                # available to this API key (e.g. a typo'd GEMINI_MODEL, or a model not yet
                # available on the v1beta REST API) — distinct from "key rejected" or a
                # generic error so the fix (correct GEMINI_MODEL) is obvious to the user.
                _log_gemini_failure(
                    purpose, self.model, 404, "model_not_found", "Configured Gemini model was not found.", _google_error_status(resp)
                )
                raise GeminiUnavailableError(
                    f"Gemini model '{self.model}' was not found. Check GEMINI_MODEL in backend/.env.",
                    reason="model_not_found",
                )

            if resp.status_code >= 500:
                if attempt < max_retries - 1:
                    logger.info(
                        "Gemini server error, retrying: provider=gemini model=%s purpose=%s status=%s attempt=%s/%s",
                        self.model,
                        purpose,
                        resp.status_code,
                        attempt + 1,
                        max_retries,
                    )
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                _log_gemini_failure(purpose, self.model, resp.status_code, "server_error", "Gemini server error after retries.")
                raise GeminiUnavailableError(f"Gemini returned a server error ({resp.status_code}).", reason="server_error")

            if resp.status_code != 200:
                _log_gemini_failure(
                    purpose, self.model, resp.status_code, "error", "Unexpected status code.", _google_error_status(resp)
                )
                raise GeminiUnavailableError(f"Gemini returned status {resp.status_code}.", reason="error")

            try:
                data = resp.json()
                candidates = data.get("candidates") or []
                if not candidates:
                    block_reason = (data.get("promptFeedback") or {}).get("blockReason")
                    if block_reason:
                        _log_gemini_failure(purpose, self.model, 200, "blocked", f"blockReason={block_reason}")
                        raise GeminiUnavailableError(f"Gemini blocked the request ({block_reason}).", reason="blocked")
                    _log_gemini_failure(purpose, self.model, 200, "empty_response", "No candidates in response.")
                    raise GeminiUnavailableError("Gemini returned no response candidates.", reason="empty_response")
                parts = (candidates[0].get("content") or {}).get("parts") or []
                text = "".join(p.get("text", "") for p in parts).strip()
                if not text:
                    _log_gemini_failure(purpose, self.model, 200, "empty_response", "Candidate had no text.")
                    raise GeminiUnavailableError("Gemini returned an empty response.", reason="empty_response")
                return text
            except GeminiUnavailableError:
                raise
            except Exception as exc:
                _log_gemini_failure(purpose, self.model, 200, "error", f"Could not parse response: {type(exc).__name__}.")
                raise GeminiUnavailableError(f"Could not parse Gemini's response: {type(exc).__name__}.", reason="error") from exc

        raise GeminiUnavailableError("Gemini rate limit exceeded after retries.", reason="rate_limited")

    def generate_insight(self, context: dict) -> str:
        user_prompt = (
            "Here is the verified structured analysis context in JSON:\n\n"
            f"{json.dumps(context, indent=2, default=str)}\n\n"
            "Write a short business-friendly explanation of these results, "
            "highlighting the most important findings and 1-3 possible business "
            "recommendations grounded strictly in the numbers above."
        )
        return self._generate(user_prompt, SYSTEM_PROMPT, purpose="insights")

    def generate_report_insight(self, context: dict) -> str:
        user_prompt = (
            "Here is the verified structured analysis context for a business analytics report, "
            "in JSON:\n\n"
            f"{json.dumps(context, indent=2, default=str)}\n\n"
            "Write the 'AI Business Insights' section of this report using EXACTLY these five "
            "Markdown headings, in this order, each with 2-4 concise bullet points grounded "
            "strictly in the numbers above (skip a heading's bullets only if the context truly "
            "has nothing relevant for it, but keep the heading):\n\n"
            "## Key Business Insights\n"
            "## Recommendations\n"
            "## Areas to Investigate\n"
            "## Potential Risks\n"
            "## Suggested Next Actions\n\n"
            "Do not repeat the raw numbers verbatim in every bullet — interpret what they mean "
            "for the business. Stay under 300 words total."
        )
        return self._generate(user_prompt, SYSTEM_PROMPT, purpose="report_insight")

    def answer_question(self, context: dict, question: str, history: list[dict] | None = None) -> str:
        if not self.is_configured():
            raise GeminiUnavailableError(
                "GEMINI_API_KEY is not set. Add it to backend/.env to enable AI Chat.", reason="not_configured"
            )
        history_text = ""
        if history:
            history_text = "\n\nPrevious conversation (use this to resolve pronouns/follow-ups like " \
                "'it' or 'that model'):\n" + "\n".join(f"{m['role']}: {m['content']}" for m in history[-6:])
        user_prompt = (
            "Here is the verified structured context for the user's CURRENT project (JSON) — "
            "use it when the question is about this project's dataset, analysis, model, or "
            "results:\n\n"
            f"{json.dumps(context, indent=2, default=str)}\n"
            f"{history_text}\n\n"
            f"Question: {question}\n\n"
            "How to answer:\n"
            "- If this question is about the current project (its data, findings, model, "
            "predictions, or results), answer using ONLY the verified numbers in the context "
            "above — never invent a project-specific statistic. If the context doesn't "
            "contain what's needed, say so honestly instead of guessing.\n"
            "- If this is a general data-science, statistics, or machine-learning question "
            "not asking about a specific number from this project (e.g. 'what is "
            "overfitting?', 'explain cross-validation'), answer it using your own knowledge "
            "— it does not need to be grounded in the context above."
        )
        return self._generate(user_prompt, SYSTEM_PROMPT, purpose="chat")


gemini_service = GeminiService()
