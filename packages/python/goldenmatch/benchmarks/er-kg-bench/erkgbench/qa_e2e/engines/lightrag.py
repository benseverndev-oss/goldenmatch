"""LightRAG QA engine adapter over the real async API (ainsert/aquery), driven
from the sync QAEngine protocol via asyncio.run. LightRAG owns its LLM calls, so
the cost seam is an injected counting llm_model_func."""
from __future__ import annotations

import asyncio
import tempfile
import time
from typing import Any

from ..harness import AnswerResult, BuildResult


def make_counting_llm_func(inner, counter: dict):
    """Wrap a LightRAG llm_model_func to estimate token usage (len//4) into
    `counter` ({'in','out'}). Signature matches LightRAG's contract."""

    async def _wrapped(prompt, system_prompt=None, history_messages=None, **kwargs):
        counter["in"] += max(1, (len(prompt) + len(system_prompt or "")) // 4)
        out = await inner(
            prompt,
            system_prompt=system_prompt,
            history_messages=history_messages,
            **kwargs,
        )
        counter["out"] += max(1, len(out) // 4)
        return out

    return _wrapped


class LightRAGQAEngine:
    name = "lightrag"
    fidelity = "real-e2e"

    def __init__(
        self, *, llm_model_func: Any, embedding_func: Any, work_root: str | None = None
    ):
        self._counter = {"in": 0, "out": 0}
        self._llm_func = make_counting_llm_func(llm_model_func, self._counter)
        self._embedding_func = embedding_func
        self._work_root = work_root

    def _new_rag(self, working_dir: str):
        from lightrag import LightRAG

        return LightRAG(
            working_dir=working_dir,
            llm_model_func=self._llm_func,
            embedding_func=self._embedding_func,
        )

    def build_kg(self, corpus) -> BuildResult:
        t0 = time.perf_counter()
        before = dict(self._counter)
        workdir = tempfile.mkdtemp(prefix="lightrag_", dir=self._work_root)
        rag = self._new_rag(workdir)

        async def _build():
            await rag.initialize_storages()
            for doc in corpus.documents:
                await rag.ainsert(doc.text)

        asyncio.run(_build())
        handle = {"rag": rag, "workdir": workdir}
        return BuildResult(
            handle=handle,
            input_tokens=self._counter["in"] - before["in"],
            output_tokens=self._counter["out"] - before["out"],
            latency_s=time.perf_counter() - t0,
        )

    def answer(self, handle, question: str) -> AnswerResult:
        from lightrag import QueryParam

        t0 = time.perf_counter()
        before = dict(self._counter)
        rag = handle["rag"]
        text = asyncio.run(rag.aquery(question, param=QueryParam(mode="hybrid")))
        return AnswerResult(
            text=text or "",
            retrieved_fact_ids=(),  # LightRAG doesn't surface retrieved ids; see spec note
            input_tokens=self._counter["in"] - before["in"],
            output_tokens=self._counter["out"] - before["out"],
            latency_s=time.perf_counter() - t0,
        )
