import json
import logging

import httpx

from app.core.config import settings
from app.services.ai_prompts import SYSTEM_PROMPT

logger = logging.getLogger("ollama_service")


class OllamaUnavailableError(Exception):
    pass


class OllamaService:
    def __init__(self):
        self.base_url = settings.OLLAMA_BASE_URL.rstrip("/")
        self.model = settings.OLLAMA_MODEL
        self.timeout = settings.OLLAMA_TIMEOUT_SECONDS

    def is_available(self) -> bool:
        return self.check_connection()["available"]

    def check_connection(self) -> dict:
        """Diagnoses exactly why Ollama is unreachable (server down, model not installed,
        timeout) instead of a flat yes/no, so the Settings 'AI Status' card and startup
        logs can tell a user what to actually fix."""
        base = {"url": self.base_url, "model": self.model}
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(f"{self.base_url}/api/tags")
        except httpx.TimeoutException:
            return {**base, "available": False, "reason": "timeout", "detail": f"Connecting to Ollama at {self.base_url} timed out."}
        except httpx.ConnectError:
            return {
                **base,
                "available": False,
                "reason": "unreachable",
                "detail": (
                    f"Could not connect to Ollama at {self.base_url}. If the backend runs in Docker, "
                    "OLLAMA_BASE_URL must be http://ollama:11434 (Ollama in Compose) or "
                    "http://host.docker.internal:11434 (Ollama on the host), not localhost."
                ),
            }
        except Exception as exc:
            logger.info("Ollama availability check failed: %s", type(exc).__name__)
            return {**base, "available": False, "reason": "error", "detail": f"Unexpected error contacting Ollama: {type(exc).__name__}"}

        if resp.status_code != 200:
            return {**base, "available": False, "reason": "server_error", "detail": f"Ollama responded with status {resp.status_code}."}

        try:
            data = resp.json()
            installed_models = {m.get("name") or m.get("model") for m in data.get("models", [])}
        except Exception:
            installed_models = set()

        if self.model not in installed_models:
            return {
                **base,
                "available": False,
                "reason": "model_not_found",
                "detail": (
                    f"Ollama is reachable, but the model '{self.model}' is not installed. "
                    f"Run `ollama pull {self.model}` on the Ollama host, or update OLLAMA_MODEL to an "
                    "installed tag."
                ),
                "installed_models": sorted(m for m in installed_models if m),
            }

        return {**base, "available": True, "reason": "ok", "detail": "Ollama is reachable and the configured model is installed."}

    def _chat(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
                return data.get("message", {}).get("content", "").strip()
        except httpx.TimeoutException as exc:
            logger.warning("Ollama request timed out: %s", exc)
            raise OllamaUnavailableError("The AI service timed out.") from exc
        except httpx.HTTPStatusError as exc:
            logger.warning("Ollama returned an error status: %s", exc.response.status_code)
            raise OllamaUnavailableError(
                f"The AI model '{self.model}' may not be installed on the Ollama server."
            ) from exc
        except Exception as exc:
            logger.warning("Ollama request failed: %s", type(exc).__name__)
            raise OllamaUnavailableError("The AI service is currently unavailable.") from exc

    def generate_insight(self, context: dict) -> str:
        user_prompt = (
            "Here is the verified structured analysis context in JSON:\n\n"
            f"{json.dumps(context, indent=2, default=str)}\n\n"
            "Write a short business-friendly explanation of these results, "
            "highlighting the most important findings and 1-3 possible business "
            "recommendations grounded strictly in the numbers above."
        )
        return self._chat(SYSTEM_PROMPT, user_prompt)

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
        return self._chat(SYSTEM_PROMPT, user_prompt)

    def answer_question(self, context: dict, question: str, history: list[dict] | None = None) -> str:
        history_text = ""
        if history:
            history_text = "\n\nPrevious conversation:\n" + "\n".join(
                f"{m['role']}: {m['content']}" for m in history[-6:]
            )
        user_prompt = (
            "Here is the verified structured analytics context for this project in JSON:\n\n"
            f"{json.dumps(context, indent=2, default=str)}\n"
            f"{history_text}\n\n"
            f"Answer the following user question using only the information above. "
            f"If the context does not contain enough information to answer, say so honestly "
            f"instead of guessing.\n\nQuestion: {question}"
        )
        return self._chat(SYSTEM_PROMPT, user_prompt)


ollama_service = OllamaService()
