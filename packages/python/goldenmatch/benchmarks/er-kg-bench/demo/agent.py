"""LLM seam for the ER-KG demo. answer() is pure given an injected llm_fn, so it
is unit-tested with a stub (no network). The real llm_fn (gpt-4o-mini via openai
+ BudgetTracker) is built only when OPENAI_API_KEY is present."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from demo.kg import Subgraph  # pyright: ignore[reportMissingImports]

if TYPE_CHECKING:
    from goldenmatch.core.llm_budget import BudgetTracker

_SYSTEM = (
    "You are a knowledge-base assistant. Answer ONLY from the entities listed in "
    "the provided knowledge base. Treat each listed entity as distinct unless the "
    "knowledge base itself shows them as one entity. Do not use outside knowledge."
)


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class AgentAnswer:
    text: str
    model: str
    n_nodes_seen: int
    input_tokens: int
    output_tokens: int


def serialize_subgraph(sub: Subgraph) -> str:
    lines = [f"Knowledge base ({len(sub.nodes)} entities):"]
    for i, n in enumerate(sub.nodes, 1):
        names = ", ".join(n.names)
        lines.append(f"{i}. type={n.type}; names=[{names}]; description={n.context}")
    return "\n".join(lines)


def build_prompt(question: str, sub: Subgraph) -> str:
    return f"{_SYSTEM}\n\n{serialize_subgraph(sub)}\n\nQuestion: {question}"


def answer(question: str, sub: Subgraph, llm_fn: Callable[[str], LLMResponse]) -> AgentAnswer:
    resp = llm_fn(build_prompt(question, sub))
    return AgentAnswer(
        text=resp.text, model=resp.model, n_nodes_seen=len(sub.nodes),
        input_tokens=resp.input_tokens, output_tokens=resp.output_tokens,
    )


def make_openai_llm_fn(model: str = "gpt-4o-mini", tracker: BudgetTracker | None = None) -> Callable[[str], LLMResponse]:
    """Real LLM. Import-guarded; raises if openai/key missing. `tracker` is an
    optional goldenmatch.core.llm_budget.BudgetTracker to record cost."""
    import os

    from openai import OpenAI  # pyright: ignore[reportMissingImports]

    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY not set; cannot build the real llm_fn.")
    client = OpenAI()

    def _fn(prompt: str) -> LLMResponse:
        resp = client.chat.completions.create(
            model=model, temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        usage = resp.usage
        it, ot = (usage.prompt_tokens, usage.completion_tokens) if usage else (0, 0)
        if tracker is not None:
            tracker.record_usage(input_tokens=it, output_tokens=ot, model=model)
        return LLMResponse(text=resp.choices[0].message.content or "", model=model,
                           input_tokens=it, output_tokens=ot)

    return _fn
