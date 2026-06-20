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
        client = self._ensure_client()
        resp = client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
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
