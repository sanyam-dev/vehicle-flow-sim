"""Swappable LLM providers.

Every provider implements `LLMProvider.complete(messages, schema) -> str`:
given chat messages and a JSON schema, return the model's raw JSON text.
Validation happens in the caller (Pydantic), never here, so providers stay
thin and interchangeable. Pick one with the LLM_PROVIDER setting:

* "ollama": Ollama Cloud (host OLLAMA_HOST, Bearer OLLAMA_API_KEY, model LLM_MODEL)
* "rules":  no LLM; the chat endpoint uses the rule-based fallback agent
* "fake":   scripted answers, for tests
"""

from collections.abc import Callable
from typing import Protocol

import httpx
from ollama import Client, ResponseError

from app.config import get_settings


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
    """Ollama Cloud chat completion with structured (JSON schema) output."""

    name = "ollama"

    def __init__(self, host: str, api_key: str, model: str, timeout_s: float = 60.0):
        """
        Args:
            host: API base URL, e.g. https://ollama.com.
            api_key: Ollama API key (sent as a Bearer token).
            model: Cloud model name, e.g. "glm-5.3-flash".
            timeout_s: HTTP timeout per request.
        """
        self.model = model
        self.client = Client(host=host, timeout=timeout_s,
                             headers={"Authorization": f"Bearer {api_key}"})

    def complete(self, messages: list[dict], schema: dict) -> str:
        """See LLMProvider.complete. Low temperature for stable extraction."""
        try:
            resp = self.client.chat(model=self.model, messages=messages, format=schema,
                                    options={"temperature": 0.2})
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


def get_provider() -> LLMProvider | None:
    """Provider selected by settings, or None for the rule-based agent.

    Raises:
        LLMError: if the selected provider is not configured.
    """
    s = get_settings()
    if s.llm_provider == "rules":
        return None
    if s.llm_provider == "ollama":
        if not s.ollama_api_key:
            raise LLMError("OLLAMA_API_KEY is not set")
        return OllamaProvider(s.ollama_host, s.ollama_api_key, s.llm_model)
    raise LLMError(f"unknown LLM_PROVIDER {s.llm_provider!r}")
