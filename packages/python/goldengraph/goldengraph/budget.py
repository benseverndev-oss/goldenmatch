"""Slice 4c cross-controller budget: ONE ceiling threaded through build-LLM (extraction) AND
answer-LLM (classification + synthesis) via a single _BudgetedLLM wrapper.

Token counts are a deterministic provider-agnostic ESTIMATE (len//4), not a real tokenizer. The
pre-check is on INPUT only, so spent_tokens may overshoot total_tokens after a call that passed the
check -- the contract is "raise before a call whose INPUT would exceed", not exact-ceiling accounting.
"""
from __future__ import annotations

from dataclasses import dataclass


class BudgetExhausted(RuntimeError):
    """Raised by _BudgetedLLM when a call's input estimate would exceed the budget."""


@dataclass
class Budget:
    total_tokens: int | None = None      # None -> unbounded
    spent_tokens: int = 0

    def remaining(self) -> float:
        return float("inf") if self.total_tokens is None else self.total_tokens - self.spent_tokens

    def would_exceed(self, n: int) -> bool:
        return self.total_tokens is not None and self.spent_tokens + n > self.total_tokens

    def record(self, n: int) -> None:
        self.spent_tokens += n


def _est_tokens(s: str) -> int:
    return max(1, len(s) // 4)


class _BudgetedLLM:
    """Wraps an LLMClient (complete(str)->str); charges input BEFORE the call, records output after."""

    def __init__(self, llm, budget: Budget):
        self._llm = llm
        self._budget = budget

    def complete(self, prompt: str) -> str:
        est_in = _est_tokens(prompt)
        if self._budget.would_exceed(est_in):
            raise BudgetExhausted(
                f"budget exhausted: {self._budget.spent_tokens}/{self._budget.total_tokens} (+{est_in})"
            )
        out = self._llm.complete(prompt)
        self._budget.record(est_in + _est_tokens(out))
        return out

    def complete_json(self, prompt: str) -> str:
        """Budgeted JSON-constrained completion; forwards to the inner client's
        `complete_json` when present, else falls back to `complete`."""
        est_in = _est_tokens(prompt)
        if self._budget.would_exceed(est_in):
            raise BudgetExhausted(
                f"budget exhausted: {self._budget.spent_tokens}/{self._budget.total_tokens} (+{est_in})"
            )
        fn = getattr(self._llm, "complete_json", self._llm.complete)
        out = fn(prompt)
        self._budget.record(est_in + _est_tokens(out))
        return out
