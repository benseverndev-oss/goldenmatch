# Stage-2-D: Hybrid Passages via the Local nomic Embedder — Design

**Status:** Approved (brainstorm), pending implementation plan.
**Date:** 2026-06-30
**Context:** goldengraph real-corpus (stage-2) quality. Follows three connectivity/synthesis nulls —
self-consistency (2-B), cross-doc-link + surface-bridge (2-C) — all of which failed because the 7B
builds a FRAGMENTED graph on real prose and no cheap post-hoc lever can manufacture a connection the
extraction never created. This sub-project changes tack: stop trying to repair the graph, and instead
hand synthesis the raw retrieved PASSAGES (the `hybrid` answer mode), which is currently blocked on the
local-OSS stack only because its passage embedder is hardcoded to OpenAI.

## Goal

Unblock goldengraph's `hybrid` answer mode on the local stack by routing the passage embedder through
the local nomic endpoint (the existing `OPENAI_BASE_URL`→Ollama redirect) instead of the hardcoded
OpenAI model, then measure whether passage-augmented synthesis beats graph-only `local` on real-corpus
multi-hop.

## Why this lever (after three nulls)

`support_recall ≈ 0.6–0.7` proves the supporting passages ARE retrieved; the failure is that we reduce
them to triples and fragment the graph. `hybrid` mode hands synthesis BOTH the subgraph AND the raw
passages, so the model can read the answer from the text even when the graph chain is broken — it
**sidesteps** the construction ceiling instead of fixing it. MuSiQue is a reading-comprehension
benchmark; the answer is in those paragraphs. The hybrid synthesis prompt (`synthesize_hybrid`) and the
`_PassageRetriever` already exist; the ONLY blocker is the passage embedder.

## Architecture

One change in the bench engine, plus a mode flag at run time.

### The change (`engines/goldengraph.py`, hybrid block ~line 233)

```python
# was: adapter = _OpenAIEmbedderAdapter(OpenAI(), "text-embedding-3-large")
model = _passage_embed_model()                       # OPENAI_EMBED_MODEL or "text-embedding-3-large"
adapter = _OpenAIEmbedderAdapter(OpenAI(), model)
```

with a tiny module-level helper (mirrors the existing `run_qa_e2e._rag_embed_model`):

```python
def _passage_embed_model() -> str:
    """Passage-retriever embedding model: the local lane's OPENAI_EMBED_MODEL (e.g. nomic-embed-text via
    Ollama) when set, else the OpenAI default. Routes the passage half through the SAME endpoint as the
    chat/graph halves on the local stack, unblocking hybrid mode without OpenAI spend."""
    import os
    return os.environ.get("OPENAI_EMBED_MODEL") or "text-embedding-3-large"
```

`OpenAI()` inherits `OPENAI_BASE_URL` + `OPENAI_API_KEY` from env, which the local lane already points at
Ollama with the dummy `ollama` key (the same path the chat client uses). So the passage adapter embeds
the corpus once and each query via nomic-on-Ollama. **Off the local lane (real OpenAI, env unset) it
falls back to `text-embedding-3-large` exactly as today — back-compat preserved.**

### Selecting hybrid

Run with `GOLDENGRAPH_QA_MODE=hybrid`. The engine reads it into `_retrieval_mode`
(`engines/goldengraph.py:192`), and `ask` takes the hybrid synthesis path (`synthesize_hybrid` over the
subgraph + passages). No other change: the graph half, provenance, and synthesis prompt are untouched.

## Data flow

```
ask(mode="hybrid")
  -> seed_by_query + _retrieve_local        (the graph half, unchanged)
  -> passages.retrieve(query, passage_k)     (nomic-embedded passage half, now unblocked)
  -> synthesize_hybrid(subgraph, passages)   (existing prompt)
```

## Error handling / back-compat

- Env unset → `text-embedding-3-large` (today's behavior; the OpenAI head-to-head lane is unaffected).
- Only fires in the `hybrid` branch; `local` mode never builds the passage retriever (unchanged).
- Reuses the existing `_CachingEmbedder` so the corpus embeds once across all questions.

## Testing

- **Pure unit test of `_passage_embed_model()`** (box-safe, no OpenAI/Ollama): env set → `nomic-embed-text`;
  env unset → `text-embedding-3-large`. That is the one logic branch this sub-project adds.
- **No-regression:** existing bench-engine tests still pass (the change is additive; non-hybrid paths
  are byte-identical).
- **Integration validation** = the N=20 MuSiQue hybrid run (the full passage path needs real
  embeddings, so it is a Modal check, not local).

## Scope / YAGNI

- **Only the passage-embedder model routing** + running with `GOLDENGRAPH_QA_MODE=hybrid`.
- **No change** to `synthesize_hybrid`, the graph half, the prompt, provenance, or the
  `goldenmatch_rag`/`text_rag` baseline engines (they stay OpenAI — this is goldengraph's passage half
  only).
- **No new passage embedder** — reuse nomic via the existing redirect.

## Validation gate + framing (the load-bearing caveats)

Two honest caveats are part of the verdict, not footnotes:

1. **Hybrid is graph-guided RAG, not pure KG.** A hybrid win shows goldengraph WITH passage augmentation
   works on real corpus — it sidesteps the construction ceiling (reads from text) rather than fixing the
   graph. A legitimate product mode, but a different posture than "the KG reasons multi-hop." The report
   states this plainly.
2. **nomic passages are a LOWER BOUND.** nomic (768-dim) is a weaker passage embedder than the original
   `text-embedding-3-large` (3072-dim), so hybrid-on-nomic understates hybrid's ceiling: if it already
   helps, good; if flat, it could be weak nomic passage retrieval rather than hybrid itself (though
   `support_recall ≈ 0.6–0.7` says passages are findable).

**Run:** N=20 MuSiQue, `GOLDENGRAPH_QA_MODE=hybrid`, vs the matched `local` N=20 baseline (the stage-2-C
bridge-OFF control).

**Outcomes:**
- **Hybrid > local** → passages carry the answer; ship hybrid as the recommended real-corpus mode (with
  the RAG-posture caveat). The cheap-lever drought breaks.
- **Hybrid ≈ local** → passages don't rescue it (7B synthesis can't exploit them, or nomic passage
  retrieval is too weak) → the construction ceiling is deep; **32B extractor** is the next experiment
  (the engineered-corpus 32B≤7B counter-evidence was one closed-vocab case and may not transfer to real
  prose). No tuning — the run decides.

## Files

- Modify: `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/engines/goldengraph.py`
  (`_passage_embed_model()` helper + use it in the hybrid block).
- Create: a unit test for `_passage_embed_model()` under the bench `tests/`.
- Validation: existing `scripts/distill/modal_bench.py --corpus musique` with `GOLDENGRAPH_QA_MODE=hybrid`.
- Report: `docs/superpowers/reports/2026-06-30-stage2d-hybrid-nomic-passages.md`.
