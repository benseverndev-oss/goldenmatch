"""GoldenGraph QA engine adapter: extract -> resolve+ingest -> ask, behind the
QAEngine protocol. LLM, embedder, and resolver are injected (stubs in tests; the
real OpenAIClient + GoldenmatchEmbedder + default goldenmatch resolver in the
opt-in lane). goldengraph + its native PyStore are imported lazily so importing
this module for the registry never drags the wheel."""
from __future__ import annotations

import time
from typing import Any

from ..harness import AnswerResult, BuildResult

#: as-of coordinates large enough to see every appended batch (ingest uses at=i+1).
_AS_OF = 10**12


class _CountingLLM:
    """Wraps any goldengraph LLMClient and estimates token usage per .complete
    call (len//4), so the bench owns cost accounting regardless of the client."""

    def __init__(self, inner: Any):
        self._inner = inner
        self.input_tokens = 0
        self.output_tokens = 0

    def complete(self, prompt: str) -> str:
        self.input_tokens += max(1, len(prompt) // 4)
        out = self._inner.complete(prompt)
        self.output_tokens += max(1, len(out) // 4)
        return out


class GoldenGraphQAEngine:
    name = "goldengraph"
    fidelity = "real-e2e"

    def __init__(self, *, llm: Any, embedder: Any, resolver: Any | None = None):
        self._llm = _CountingLLM(llm)
        self._embedder = embedder
        self._resolver = resolver  # None -> ingest uses the goldenmatch-backed default

    def build_kg(self, corpus) -> BuildResult:
        from goldengraph import ingest
        from goldengraph_native import _native as ggn

        t0 = time.perf_counter()
        before_in, before_out = self._llm.input_tokens, self._llm.output_tokens
        store = ggn.PyStore()
        for i, doc in enumerate(corpus.documents):
            ingest(doc.text, store, at=i + 1, llm=self._llm, resolver=self._resolver)
        handle = {"store": store, "valid_t": _AS_OF, "tx_t": _AS_OF}
        return BuildResult(
            handle=handle,
            input_tokens=self._llm.input_tokens - before_in,
            output_tokens=self._llm.output_tokens - before_out,
            latency_s=time.perf_counter() - t0,
        )

    def answer(self, handle, question: str) -> AnswerResult:
        from goldengraph.answer import ask

        t0 = time.perf_counter()
        before_in, before_out = self._llm.input_tokens, self._llm.output_tokens
        text = ask(
            question,
            handle["store"],
            llm=self._llm,
            embedder=self._embedder,
            valid_t=handle["valid_t"],
            tx_t=handle["tx_t"],
            mode="local",
        )
        return AnswerResult(
            text=text,
            retrieved_fact_ids=(),  # see support-recall note in the plan/spec
            input_tokens=self._llm.input_tokens - before_in,
            output_tokens=self._llm.output_tokens - before_out,
            latency_s=time.perf_counter() - t0,
        )
