"""Provider-agnostic LLM boundary for goldengraph extraction.

Tests inject a deterministic stub; the optional `OpenAIClient` (behind the
`[openai]` extra) is the only real provider shipped. goldengraph owns this
minimal protocol rather than coupling to goldenmatch's internal LLM client, so
extraction stays testable + provider-swappable.
"""

from __future__ import annotations

import os
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
            import os

            import openai  # lazy: only needed for the real provider

            # The bench workflow sets OPENAI_BASE_URL='' on the OpenAI-API path (it
            # is only non-empty for a local Ollama run). The openai SDK treats an
            # empty-string base_url as a literal (invalid) URL -> APIConnectionError,
            # and passing base_url=None does NOT help -- the SDK re-reads the empty
            # env var itself. So pass an explicit default when the env is empty.
            base_url = os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
            api_key = os.environ.get("OPENAI_API_KEY") or None
            self._client = openai.OpenAI(base_url=base_url, api_key=api_key)
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
        # Optional fixed seed for reproducible decoding (GOLDENGRAPH_LLM_SEED). Ollama's
        # OpenAI-compatible endpoint honors `seed` + temperature=0; unset/non-int -> omitted
        # (unchanged behavior). Reduces run-to-run extraction variance for bench determinism.
        _raw_seed = os.environ.get("GOLDENGRAPH_LLM_SEED", "").strip()
        if _raw_seed:
            try:
                kwargs["seed"] = int(_raw_seed)
            except ValueError:
                pass
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
