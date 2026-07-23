"""GoldenMatch-native RAG engines -- does goldenmatch's OWN retrieval surface beat
naive text-RAG, and does its entity-resolution layer add anything?

The head-to-head had a no-KG control (`text_rag`) that reimplements naive retrieval
with the raw `openai` SDK -- but goldenmatch SHIPS a first-class retrieval surface
(`retrieve_similar_records`, `entity_aware_retrieve`) that nothing in the bench used.
These two engines close that gap:

  - `goldenmatch_rag`     -> goldenmatch's `retrieve_similar_records` + the SAME
                            synthesis text_rag uses. Isolates goldenmatch's retrieval
                            MECHANICS (embed -> ANNBlocker top-k) vs OpenAI's.
  - `goldenmatch_entity_rag` -> `entity_aware_retrieve` (retrieve -> dedupe ->
                            canonicalize) + the same synthesis. Isolates what the
                            entity-resolution layer ADDS on top of plain retrieval.

Both hold the EMBEDDER constant -- OpenAI `text-embedding-3-large`, the same model
text_rag and ms_graphrag use -- via an injected adapter, so any delta vs text_rag is
attributable to goldenmatch's retrieval/resolution layer, NOT a different embedder.
The adapter caches by `cache_key` so the corpus is embedded once across all questions
(matching text_rag's build-once cost), and the resolve step is pinned to a
deterministic fuzzy token_sort (no auto-config embedding scorer -> no torch in the
light lane). The OpenAI client is injectable so the wiring is testable offline."""
from __future__ import annotations

import os
import threading
import time
from typing import Any

import numpy as np

from ..harness import AnswerResult, BuildResult
from .text_rag import _PROMPT, _extract_answer

_EMBED_BATCH = 256
# Force the resolve step onto a deterministic fuzzy token_sort match on the passage
# text. Passing `fuzzy=` makes dedupe_df build an EXPLICIT config (skips auto-config),
# so it never routes the long text column to an embedding scorer (which would need
# torch / sentence-transformers, absent in the light lane).
_RESOLVE_FUZZY = {"text": 0.85}


class _OpenAIEmbedderAdapter:
    """Wraps the OpenAI embeddings API in the `embed_column(values, cache_key)`
    shape goldenmatch's retrieval surface expects. Caches by `cache_key` so the
    corpus (same values, same key every question) is embedded exactly once."""

    def __init__(self, client: Any, model: str, *, batch: int = _EMBED_BATCH):
        self._client = client
        self._model = model
        self._batch = batch
        self._cache: dict[str, np.ndarray] = {}
        # The corpus cache_key is written on the FIRST answer; under parallel answering
        # (QA_E2E_ANSWER_WORKERS) several first-wave questions can hit `embed_column`
        # with the same cache_key at once, so guard the cache read/store. The network
        # embed itself runs OUTSIDE the lock (query embeds pass cache_key=None and never
        # touch it), so parallelism is preserved; the lock only makes the store atomic.
        self._cache_lock = threading.Lock()

    def embed_column(self, values, cache_key=None) -> np.ndarray:
        if cache_key is not None:
            with self._cache_lock:
                cached = self._cache.get(cache_key)
            if cached is not None:
                return cached
        texts = [v if (v and str(v).strip()) else " " for v in values]
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch):
            chunk = [str(t) for t in texts[i : i + self._batch]]
            resp = self._client.embeddings.create(model=self._model, input=chunk)
            out.extend(d.embedding for d in resp.data)
        arr = np.asarray(out, dtype=float)
        arr = arr / (np.linalg.norm(arr, axis=1, keepdims=True) + 1e-12)
        if cache_key is not None:
            with self._cache_lock:
                # First writer wins; concurrent computers produce the same array anyway.
                arr = self._cache.setdefault(cache_key, arr)
        return arr


def _make_frame(ids: list[str], texts: list[str]):
    import polars as pl

    return pl.DataFrame({"id": ids, "text": texts})


def _synthesize(client: Any, model: str, ctx: str, question: str):
    """Run the SAME synthesis prompt the other engines use; return
    (answer_text, prompt_tokens, completion_tokens)."""
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": _PROMPT.format(ctx=ctx, q=question)}],
    )
    raw = resp.choices[0].message.content or ""
    usage = getattr(resp, "usage", None)
    return (
        _extract_answer(raw),
        int(getattr(usage, "prompt_tokens", 0) or 0),
        int(getattr(usage, "completion_tokens", 0) or 0),
    )


class _BaseGoldenmatchRAGEngine:
    fidelity = "real-e2e"

    def __init__(
        self,
        *,
        model: str = "gpt-4o-mini",
        embedding_model: str = "text-embedding-3-large",
        top_k: int | None = None,
        client: Any | None = None,
    ):
        if client is None:
            from .text_rag import make_openai_client

            client = make_openai_client()
        self._client = client
        self._model = model
        self._embedder = _OpenAIEmbedderAdapter(client, embedding_model)
        self._top_k = (
            top_k
            if top_k is not None
            else int(os.environ.get("GOLDENMATCH_RAG_TOP_K", "10"))
        )

    def build_kg(self, corpus) -> BuildResult:
        t0 = time.perf_counter()
        ids = [d.id for d in corpus.documents]
        texts = [d.text for d in corpus.documents]
        handle = {"frame": _make_frame(ids, texts), "n": len(ids)}
        return BuildResult(handle=handle, latency_s=time.perf_counter() - t0)


class GoldenmatchRAGQAEngine(_BaseGoldenmatchRAGEngine):
    """goldenmatch `retrieve_similar_records` + shared synthesis."""

    name = "goldenmatch_rag"

    def answer(self, handle, question: str) -> AnswerResult:
        t0 = time.perf_counter()
        if not handle or not handle.get("n"):
            return AnswerResult(text="", latency_s=time.perf_counter() - t0)
        from goldenmatch.core.retrieval import retrieve_similar_records

        hits = retrieve_similar_records(
            handle["frame"], question, "text", k=self._top_k, embedder=self._embedder
        )
        if not hits:
            return AnswerResult(text="", latency_s=time.perf_counter() - t0)
        ctx = "\n\n".join(f"[{i + 1}] {h.record.get('text', '')}" for i, h in enumerate(hits))
        text, in_tok, out_tok = _synthesize(self._client, self._model, ctx, question)
        return AnswerResult(
            text=text,
            retrieved_fact_ids=tuple(str(h.record.get("id")) for h in hits),
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_s=time.perf_counter() - t0,
        )


class GoldenmatchEntityRAGQAEngine(_BaseGoldenmatchRAGEngine):
    """goldenmatch `entity_aware_retrieve` (retrieve -> dedupe -> canonicalize) +
    shared synthesis. Deterministic canonicalization (no `llm_call`), deterministic
    fuzzy resolve -- so the ONLY new variable vs `goldenmatch_rag` is the
    entity-resolution layer."""

    name = "goldenmatch_entity_rag"

    def answer(self, handle, question: str) -> AnswerResult:
        t0 = time.perf_counter()
        if not handle or not handle.get("n"):
            return AnswerResult(text="", latency_s=time.perf_counter() - t0)
        from goldenmatch.core.rag_surface import entity_aware_retrieve

        result = entity_aware_retrieve(
            handle["frame"],
            question,
            "text",
            k=self._top_k,
            embedder=self._embedder,
            fuzzy=_RESOLVE_FUZZY,
        )
        if not result.entities:
            return AnswerResult(text="", latency_s=time.perf_counter() - t0)
        ctx = "\n\n".join(
            f"[{i + 1}] {e.record.get('text', '')}" for i, e in enumerate(result.entities)
        )
        text, in_tok, out_tok = _synthesize(self._client, self._model, ctx, question)
        ids = tuple(
            str(m.record.get("id")) for e in result.entities for m in e.members
        )
        return AnswerResult(
            text=text,
            retrieved_fact_ids=ids,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_s=time.perf_counter() - t0,
        )
