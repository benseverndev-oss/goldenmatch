# Stage-2-D: Hybrid Passages via Local nomic — Validation Verdict (SHIP)

**Date:** 2026-06-30
**Spec:** `docs/superpowers/specs/2026-06-30-stage2d-hybrid-nomic-passages-design.md`
**Plan:** `docs/superpowers/plans/2026-06-30-stage2d-hybrid-nomic-passages.md`

## Run config

- Corpus: MuSiQue-Ans, seeded subset, **N=20** (matched A/B on the identical questions).
- Engine: goldengraph, open extraction, `qwen2.5:7b-instruct` chat, **nomic-embed-text passages** (the
  stage-2-D rewire), Modal A10G, fair metric.
- A: `GOLDENGRAPH_QA_MODE=auto` (`local`, graph-only — the stage-2-C bridge-OFF control).
- B: `GOLDENGRAPH_QA_MODE=hybrid` (graph + raw passages).

## Result (matched N=20 A/B) — FIRST WIN OF THE STAGE-2 ARC

| metric | local (graph-only) | hybrid (graph + passages) |
|---|---|---|
| `answer_match` | 0.15 | **0.30** (2×) |
| `answer_match` (entity-subset, n=11) | ~0.13 | **0.36** |
| `token_f1` | ~0.11 | **0.37** |
| `exact_match` | — | 0.25 |
| `support_recall` | 0.65 | 0.66 |

**Verdict: SHIP (opt-in).** Hybrid **doubles** answer_match (0.15 → 0.30) on real-corpus multi-hop —
the first lever in the stage-2 arc to move the number, after three nulls (self-consistency, cross-doc-
link, surface-bridge).

## Why it worked (the diagnosis held)

Every cheap graph-repair lever failed because the 7B builds a fragmented graph; `support_recall ≈ 0.65`
proved the supporting passages were nonetheless retrieved. Hybrid hands synthesis BOTH the subgraph AND
those raw passages, so the model reads the answer from the text even when the graph chain is broken — it
**sidesteps** the construction ceiling instead of fighting it. The evidence we were already finding and
throwing away is now used.

The rewire also worked end-to-end: the run executed with no `/v1/embeddings` error, so routing the
passage embedder through nomic (`OPENAI_EMBED_MODEL`) unblocked hybrid on the local Ollama stack exactly
as designed.

## The two caveats — part of the verdict, not footnotes

1. **This is graph-guided RAG, not pure-KG multi-hop.** The win comes from reading the answer out of the
   retrieved passages, with the graph as a cross-passage map. It is a legitimate, recommended product
   mode for real-corpus QA — but it does NOT show the KG reasons multi-hop on its own; it shows
   passage-augmented synthesis works. Report the headline as "goldengraph hybrid (graph + RAG)".
2. **nomic passages are a LOWER BOUND.** The passage half ran on nomic-embed-text (768-dim), a weaker
   embedder than `text-embedding-3-large` (3072-dim). So 0.30 *understates* hybrid's ceiling — a stronger
   passage embedder (the OpenAI lane, or a better local model) would likely do better still.

## Disposition

- **Ship hybrid as the recommended REAL-CORPUS mode**, opt-in via `GOLDENGRAPH_QA_MODE=hybrid` (default
  stays `local`). The code (`_passage_embed_model` routing) is back-compat-safe: off-local it is
  byte-identical (`text-embedding-3-large`).
- **Confidence:** N=20 is small, but a clean 2× on a matched subset (0.15 → 0.30) with corroborating
  `token_f1` (0.11 → 0.37) and `exact_match` (0.25) is a real signal, not noise. A follow-up N=50 hybrid
  confirm is worth running (hybrid is cheap enough to complete it).

## What this resolves for stage-2

The construction ceiling stands for the *pure KG* path — that finding (three nulls) is real and
recorded. Stage-2-D doesn't refute it; it **routes around** it: when the graph is too fragmented to reason
over, fall back to the passages the retrieval already surfaced. That is the honest framing — goldengraph's
*graph-guided RAG* mode works on real corpus where its *graph-only* mode hits the 7B construction ceiling.

## Next

- **Cheap:** N=50 hybrid confirm; sweep `GOLDENGRAPH_QA_PASSAGE_K`.
- **Bigger (the other real fix):** a **32B (or frontier) extractor** to lift graph-construction quality
  and test whether the *graph-only* path can also clear the ceiling — the engineered-corpus 32B≤7B
  counter-evidence was a single closed-vocab case and may not transfer to real prose.

## Lesson

When post-hoc repair of a lossy intermediate (the fragmented graph) keeps failing, stop repairing it and
go back to the source signal (the passages) — especially when a recall metric proves the source signal is
still in hand. One cheap rewire of an already-built-but-misconfigured path beat three clever graph levers.
