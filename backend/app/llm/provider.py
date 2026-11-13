"""Swappable LLM providers.

Every provider implements `LLMProvider.complete(messages, schema) -> str`:
given chat messages and a JSON schema, return the model's raw JSON text.
Validation happens in the caller (Pydantic), never here, so providers stay
thin and interchangeable. Pick one with the LLM_PROVIDER setting:

* "ollama": an Ollama server at OLLAMA_HOST running LLM_MODEL. OLLAMA_AUTH says
            how to authenticate: "gcp" = Google ID token (our private Cloud Run
            Ollama service), "key" = Bearer OLLAMA_API_KEY (Ollama Cloud),
            "none" = no auth (a local `ollama serve`).
* "rules":  no LLM; the chat endpoint uses the rule-based fallback agent
* "fake":   scripted answers, for tests
"""

from collections.abc import Callable
from typing import Protocol

import httpx
from ollama import Client, ResponseError

from app.config import get_settings
from app.llm import gcp_auth


class LLMError(RuntimeError):
    """The provider could not produce an answer (auth, credits, network...)."""


class LLMProvider(Protocol):
    """Minimal interface every provider implements."""

    name: str

    def complete(self, messages: list[dict], schema: dict) -> str:
        """Return the model's JSON answer as text.

        Args:
            messages: [{"role": "system"|"user"|"assistant", "content": str}, ...].
            schema: JSON schema the answer must follow (structured output).
        Raises:
            LLMError: on any provider failure.
        """
        ...


class OllamaProvider:
    """Ollama chat completion with structured (JSON schema) output."""

    name = "ollama"

    def __init__(self, host: str, model: str, headers: dict | None = None,
                 timeout_s: float = 120.0, think: bool | None = None):
        """
        Args:
            host: API base URL, e.g. https://ollama-123.asia-southeast1.run.app.
            model: Model name, e.g. "qwen3:8b".
            headers: Extra HTTP headers (authentication).
            timeout_s: HTTP timeout per request; covers a cold start of the
                GPU service (instance start + model load).
            think: Reasoning mode for thinking models; None = model default.
        """
        self.model = model
        self.think = think
        self.client = Client(host=host, timeout=timeout_s, headers=headers or {})

    def complete(self, messages: list[dict], schema: dict) -> str:
        """See LLMProvider.complete. Low temperature for stable extraction."""
        extra = {} if self.think is None else {"think": self.think}
        try:
            resp = self.client.chat(model=self.model, messages=messages, format=schema,
                                    options={"temperature": 0.2}, **extra)
        except ResponseError as exc:
            raise LLMError(f"Ollama {exc.status_code}: {exc.error}") from exc
        except (httpx.HTTPError, ConnectionError) as exc:
            raise LLMError(f"Ollama unreachable: {type(exc).__name__}") from exc
        return resp.message.content or ""


class FakeProvider:
    """Scripted provider for tests: returns queued answers in order.

    Each answer is a JSON string, or a callable(messages) -> str. An
    LLMError instance in the queue is raised instead of answered.
    """

    name = "fake"

    def __init__(self, answers: list[str | Callable | LLMError]):
        self.answers = list(answers)
        self.calls: list[list[dict]] = []

    def complete(self, messages: list[dict], schema: dict) -> str:
        """Pop the next scripted answer (see class docstring)."""
        self.calls.append(messages)
        if not self.answers:
            raise LLMError("FakeProvider has no more answers")
        ans = self.answers.pop(0)
        if isinstance(ans, LLMError):
            raise ans
        return ans(messages) if callable(ans) else ans


def _auth_headers(auth: str, host: str, api_key: str) -> dict:
    """HTTP headers for OLLAMA_AUTH = "gcp" | "key" | "none" (see module doc).

    Raises:
        LLMError: if the credentials are missing or cannot be fetched.
    """
    if auth == "none":
        return {}
    if auth == "key":
        if not api_key:
            raise LLMError("OLLAMA_API_KEY is not set")
        return {"Authorization": f"Bearer {api_key}"}
    if auth == "gcp":
        try:
            return {"Authorization": f"Bearer {gcp_auth.id_token(host)}"}
        except httpx.HTTPError as exc:
            raise LLMError(f"no Google ID token ({type(exc).__name__})") from exc
    raise LLMError(f"unknown OLLAMA_AUTH {auth!r}")


def get_provider() -> LLMProvider | None:
    """Provider selected by settings, or None for the rule-based agent.

    Raises:
        LLMError: if the selected provider is not configured.
    """
    s = get_settings()
    if s.llm_provider == "rules":
        return None
    if s.llm_provider == "ollama":
        headers = _auth_headers(s.ollama_auth, s.ollama_host, s.ollama_api_key)
        return OllamaProvider(s.ollama_host, s.llm_model, headers,
                              timeout_s=s.llm_timeout_s, think=s.llm_think)
    raise LLMError(f"unknown LLM_PROVIDER {s.llm_provider!r}")
