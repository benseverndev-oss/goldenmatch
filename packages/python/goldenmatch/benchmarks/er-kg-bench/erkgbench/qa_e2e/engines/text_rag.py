"""Naive text-RAG baseline -- the no-knowledge-graph CONTROL for the head-to-head.

The honest yardstick the program was missing: chunk = paragraph, embed every doc,
retrieve top-K by query-embedding cosine, hand the passages to the SAME model the KG
engines use, and answer. NO extraction, NO resolution, NO graph traversal -- so the
gap (positive OR negative) between this and goldengraph / lightrag / ms_graphrag /
graphiti is exactly what the KG structure buys (or costs) on multi-hop QA. Without
it, every KG judge score is unanchored.

Neutral by construction: uses the `openai` SDK directly (text-embedding-3-large +
gpt-4o-mini, matching ms_graphrag's models), not any engine's own stack. The client
is injectable so the wiring is testable without a network."""
from __future__ import annotations

import os
import time
from typing import Any

import numpy as np

from ..harness import AnswerResult, BuildResult

_EMBED_BATCH = 256


def make_openai_client():
    """Construct an OpenAI client whose base_url is resolved the SAME guarded way as the
    judge/chat clients (`run_qa_e2e._make_judge`). A bare `OpenAI()` reads `OPENAI_BASE_URL`
    from the env, and the paid head_to_head lane sets it to the EMPTY string (chat +
    embeddings both to OpenAI directly). The SDK uses that empty value verbatim, producing a
    protocol-less URL that fails every request with `httpx.UnsupportedProtocol` -- which, on
    the embedding path, silently collapses retrieval. `'' or <default>` falls through to the
    OpenAI default; a non-empty value (the local nomic/Ollama lane) is honored as-is."""
    from openai import OpenAI

    base = os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    key = os.environ.get("OPENAI_API_KEY") or None
    return OpenAI(base_url=base, api_key=key)


_PROMPT = (
    "Answer the question using ONLY the passages below. These questions are often "
    "MULTI-HOP -- you may need to combine facts from several passages. Give the "
    "SHORTEST exact answer (an entity, name, date, or short phrase) and nothing "
    "else on the last line, prefixed 'Answer: '. Show brief reasoning first if "
    "helpful.\n\nPassages:\n{ctx}\n\nQuestion: {q}\n"
)


def _extract_answer(text: str) -> str:
    """Pull the final answer (the last `Answer:` line); fall back to the last
    non-empty line. Mirrors the goldengraph adapter so the metric sees the same
    answer SHAPE across engines (the format-fair judge handles the rest)."""
    if not text or not text.strip():
        return text
    idx = text.lower().rfind("answer:")
    if idx != -1:
        tail = text[idx + len("answer:"):].lstrip()
        return tail.splitlines()[0].strip() if tail else ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else text.strip()


class TextRAGQAEngine:
    name = "text_rag"
    fidelity = "real-e2e"

    def __init__(
        self,
        *,
        model: str = "gpt-4o-mini",
        embedding_model: str = "text-embedding-3-large",
        top_k: int | None = None,
        client: Any | None = None,
    ):
        # Lazy client construction so importing this module for the registry never
        # needs an API key; tests inject a stub.
        if client is None:
            client = make_openai_client()
        self._client = client
        self._model = model
        self._embedding_model = embedding_model
        self._top_k = (
            top_k if top_k is not None else int(os.environ.get("TEXT_RAG_TOP_K", "10"))
        )

    def _embed(self, texts: list[str]) -> np.ndarray:
        out: list[list[float]] = []
        for i in range(0, len(texts), _EMBED_BATCH):
            chunk = [t if (t and t.strip()) else " " for t in texts[i : i + _EMBED_BATCH]]
            resp = self._client.embeddings.create(model=self._embedding_model, input=chunk)
            out.extend(d.embedding for d in resp.data)
        arr = np.asarray(out, dtype=float)
        return arr / (np.linalg.norm(arr, axis=1, keepdims=True) + 1e-12)

    def build_kg(self, corpus) -> BuildResult:
        t0 = time.perf_counter()
        ids = [d.id for d in corpus.documents]
        texts = [d.text for d in corpus.documents]
        unit = self._embed(texts) if texts else np.zeros((0, 1))
        handle = {"ids": ids, "texts": texts, "unit": unit}
        return BuildResult(handle=handle, latency_s=time.perf_counter() - t0)

    def answer(self, handle, question: str) -> AnswerResult:
        t0 = time.perf_counter()
        texts = handle["texts"]
        unit = handle["unit"]
        if not texts:
            return AnswerResult(text="", latency_s=time.perf_counter() - t0)
        qn = self._embed([question])[0]
        sims = unit @ qn
        k = min(self._top_k, len(texts))
        top = list(np.argsort(-sims)[:k])
        ctx = "\n\n".join(f"[{i + 1}] {texts[j]}" for i, j in enumerate(top))
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": _PROMPT.format(ctx=ctx, q=question)}],
        )
        raw = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        return AnswerResult(
            text=_extract_answer(raw),
            # text-RAG CAN surface its retrieved ids -> support_recall is real here
            # (unlike the KG engines, whose ball-source-ids aren't wired through).
            retrieved_fact_ids=tuple(handle["ids"][j] for j in top),
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            latency_s=time.perf_counter() - t0,
        )
