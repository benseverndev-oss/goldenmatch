"""LightRAG QA engine adapter over the real async API (ainsert/aquery), driven
from the sync QAEngine protocol on a SINGLE persistent event loop. LightRAG owns
its LLM calls, so the cost seam is an injected counting llm_model_func.

Loop discipline (the 2026-06-21 "Query failed: ... bound to a different event
loop" bug): `initialize_storages` binds LightRAG's internal asyncio locks +
priority queues to the running loop. The old adapter ran each call under its own
`asyncio.run`, so the storages built under the build loop were bound to a loop
that was already closed by query time -> every query crashed and the engine scored
0. The fix is one loop for the engine's lifetime: build + all queries run on it, so
the storages stay bound to a live loop. (Graphiti dodges this by reconnecting a
fresh client per call; LightRAG holds the index in-process, so a shared loop is the
cleaner fit.)"""
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
    # NOT safe to answer in parallel: answer() runs on a SINGLE persistent asyncio loop
    # (reused across build + every query; `run_until_complete` cannot be driven from
    # multiple threads at once) and attributes per-question tokens via a before/after
    # delta on the shared `self._counter` (double-counts + corrupts the total under
    # concurrency). The harness forces sequential answering for this engine
    # (QA_E2E_ANSWER_WORKERS is ignored).
    answer_parallel_safe = False

    def __init__(
        self, *, llm_model_func: Any, embedding_func: Any, work_root: str | None = None
    ):
        self._counter = {"in": 0, "out": 0}
        self._llm_func = make_counting_llm_func(llm_model_func, self._counter)
        self._embedding_func = embedding_func
        self._work_root = work_root
        self._loop: asyncio.AbstractEventLoop | None = None

    def _run(self, coro):
        """Run `coro` on this engine's single persistent loop. The loop is created
        lazily and reused across build + every query so LightRAG's loop-bound
        storages never outlive their loop."""
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        return self._loop.run_until_complete(coro)

    def _new_rag(self, working_dir: str):
        import os

        from lightrag import LightRAG

        # NOTE (2026-06-29): this cosine-threshold lever did NOT fix LightRAG's local-7B '[no-context]'
        # failure -- measured: naive mode returns '[no-context]' identically with the threshold at 0.05
        # vs the default, so the empty retrieval is NOT a threshold filter. The likely cause is the
        # stored chunk embeddings being unusable on the local stack (a dim/shape mismatch between
        # lightrag's `openai_embed` wrapper and Ollama's /v1/embeddings response), but confirming it
        # needs lightrag-internal debugging out of scope for the head-to-head. LightRAG is recorded as a
        # diagnosed honest-null (see docs/oss-llm-usage.md). The knob is harmless and kept as a config
        # seam; it is not the fix. Env-tunable (GOLDENGRAPH_LIGHTRAG_COSINE, default 0.05).
        thr = float(os.environ.get("GOLDENGRAPH_LIGHTRAG_COSINE", "0.05"))
        return LightRAG(
            working_dir=working_dir,
            llm_model_func=self._llm_func,
            embedding_func=self._embedding_func,
            vector_db_storage_cls_kwargs={"cosine_better_than_threshold": thr},
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

        self._run(_build())
        handle = {"rag": rag, "workdir": workdir}
        return BuildResult(
            handle=handle,
            input_tokens=self._counter["in"] - before["in"],
            output_tokens=self._counter["out"] - before["out"],
            latency_s=time.perf_counter() - t0,
        )

    def answer(self, handle, question: str) -> AnswerResult:
        import os

        from lightrag import QueryParam

        t0 = time.perf_counter()
        before = dict(self._counter)
        rag = handle["rag"]
        # `LIGHTRAG_QUERY_MODE` (default hybrid) -- "naive" = pure vector RAG, skipping the graph +
        # keyword-extraction LLM steps. The A/B that isolates whether LightRAG's structured prompts
        # (entity-extraction format, keyword JSON) survive a weak local model: if naive answers and
        # hybrid doesn't, the graph pipeline is what the 7B can't drive.
        mode = os.environ.get("LIGHTRAG_QUERY_MODE", "hybrid")
        text = self._run(rag.aquery(question, param=QueryParam(mode=mode)))
        return AnswerResult(
            text=text or "",
            retrieved_fact_ids=(),  # LightRAG doesn't surface retrieved ids; see spec note
            input_tokens=self._counter["in"] - before["in"],
            output_tokens=self._counter["out"] - before["out"],
            latency_s=time.perf_counter() - t0,
        )
