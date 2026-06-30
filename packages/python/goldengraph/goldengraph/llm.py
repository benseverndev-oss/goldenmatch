"""Provider-agnostic LLM boundary for goldengraph extraction.

Tests inject a deterministic stub; the optional `OpenAIClient` (behind the
`[openai]` extra) is the only real provider shipped. goldengraph owns this
minimal protocol rather than coupling to goldenmatch's internal LLM client, so
extraction stays testable + provider-swappable.
"""

from __future__ import annotations

from typing import Protocol


class LLMClient(Protocol):
    """A single-shot text completion: prompt in, raw model text out."""

    def complete(self, prompt: str) -> str: ...

    # Optional: a JSON-constrained completion for structured extraction. Callers MUST
    # feature-detect with `hasattr(llm, "complete_json")` and fall back to `complete`
    # -- test stubs and the pure protocol need not implement it.
    # def complete_json(self, prompt: str) -> str: ...

    # Optional: N independent samples at a temperature, for synthesis self-consistency.
    # Callers feature-detect with `hasattr(llm, "complete_many")` and fall back to `complete`.
    # def complete_many(self, prompt: str, *, n: int, temperature: float) -> list[str]: ...


class OpenAIClient:
    """Minimal OpenAI adapter (optional `[openai]` extra). Reuses goldenmatch's
    `BudgetTracker` to cap spend when one is supplied."""

    def __init__(self, model: str = "gpt-4o-mini", *, budget=None, client=None):
        self.model = model
        self.budget = budget
        self._client = client  # injectable for tests; else lazily built

    def _ensure_client(self):
        if self._client is None:
            import openai  # lazy: only needed for the real provider

            self._client = openai.OpenAI()
        return self._client

    def complete(self, prompt: str) -> str:
        return self._chat(prompt, json_mode=False)

    def complete_json(self, prompt: str) -> str:
        """Like `complete` but constrains the model to a single JSON object
        (`response_format=json_object`). Forces valid extraction JSON from weaker
        models that otherwise emit prose/fenced/invalid output (the small-OSS-model
        failure mode). Honored by OpenAI + Ollama's OpenAI-compatible endpoint."""
        return self._chat(prompt, json_mode=True)

    def complete_many(self, prompt: str, *, n: int, temperature: float) -> list[str]:
        """N independent completions at `temperature` (for synthesis self-consistency).
        A LOOP of single calls -- Ollama's OpenAI-compatible endpoint does not reliably
        honor the `n=` param -- each token-tracked through `_chat`'s budget path."""
        return [self._chat(prompt, temperature=temperature) for _ in range(max(1, n))]

    def _chat(self, prompt: str, *, json_mode: bool = False, temperature: float = 0) -> str:
        client = self._ensure_client()
        kwargs = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content or ""
        if self.budget is not None:
            # Best-effort spend accounting; BudgetTracker raises if over cap.
            usage = getattr(resp, "usage", None)
            if usage is not None:
                self.budget.record_call(
                    getattr(usage, "prompt_tokens", 0),
                    getattr(usage, "completion_tokens", 0),
                )
        return text
