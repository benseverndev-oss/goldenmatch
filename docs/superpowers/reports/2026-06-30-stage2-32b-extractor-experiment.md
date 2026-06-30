# Stage-2: 32B Extractor Experiment — Verdict (NULL: scale is not the lever)

**Date:** 2026-06-30
**Question:** Does a stronger extractor (qwen2.5:**32b** vs **7b**) build a less-fragmented graph and
clear the real-corpus pure-KG construction ceiling identified across stage-2-B/C? Decision-grade: it
determines whether strict "first-class KG" (the graph reasons multi-hop unaided) is reachable on this
stack by scaling the model.

## Run config

- Corpus: MuSiQue-Ans, seeded subset, **N=20** (matched subset to the 7B baselines).
- Engine: goldengraph, open extraction, `GOLDENGRAPH_QA_MODE=auto` (local/graph-only path), nomic
  passages off (pure KG). Modal **A100**, `qwen2.5:32b` (q4).

## Result (matched N=20)

| signal | 7B-local | **32B-local** | 7B-hybrid (ref) |
|---|---|---|---|
| `answer_match` | 0.15 | **0.10** | 0.30 |
| graph entities | ~1787 | **1561 (−13%)** | — |
| graph components | ~320 | 328 | — |
| ent/component (higher = more connected) | 5.6 | **4.8 (worse)** | — |
| EXTRACTION-bucket failures | 10 | **15** | — |

**Verdict: NULL — a 32B extractor does NOT clear the construction ceiling.** It builds a SMALLER,
no-less-fragmented graph and answers no better.

## Confidence calibration (which numbers to trust)

- The **headline `answer_match` (0.10 vs 0.15)** is within noise at N=20 (a 1-question swing). Do not
  over-read "32B is worse" on the headline alone.
- The **construction-quality signals ARE robust** — they sum over ~400 extracted paragraphs, not 20
  questions: the 32B extracted **13% fewer entities**, with the **same component count** (so a slightly
  WORSE ent/component ratio), and pushed **more** failures into the EXTRACTION bucket. These all point
  one way: the 32B does not build a better/more-connected graph.

So the safe claim is the strong one for our purposes: **scaling the extractor 7B→32B did not improve
graph construction; if anything it extracted more conservatively (fewer entities).**

## Why (the construction problem is architectural, not scale)

The real-corpus fragmentation comes from **cross-paragraph entity inconsistency** — the same entity
phrased differently across paragraphs (coref/alias) fragments into disconnected nodes. A bigger *general*
model does not automatically resolve coreference across documents; it just extracts (here, fewer, more
conservatively). The ceiling is an **extraction-architecture** problem (cross-doc entity resolution),
not a **model-capacity** problem.

This REINFORCES the engineered-corpus finding (32B ≤ 7B, schema-canon beat 32B): it is not a single
closed-vocab fluke — "task structure / construction design beats model scale" now holds on BOTH synthetic
and real prose.

## What this resolves for the goal

- **Strict "first-class KG" (the graph reasons multi-hop unaided on real text) is NOT reachable by
  scaling the extractor model on this stack.** Model size is ruled out as the lever for the construction
  ceiling.
- **Graph-guided RAG (hybrid) is confirmed as the real-corpus path.** Two independent results now agree:
  hybrid wins (0.15→0.30) AND a stronger extractor does not (this experiment). The answer to real-corpus
  multi-hop is "read from the retrieved passages," not "build a better graph by scaling the model."

## Caveats

- N=20; one model family (qwen2.5). A different *architecture* (a model with explicit coref, or an
  extraction pipeline with a cross-document entity-resolution stage) was NOT tested — only raw scale.
  The verdict is specifically "**raw model scale** doesn't clear the ceiling," not "no extractor ever
  could."
- 32B did both extraction AND synthesis here; the headline conflates them, but the GRAPH metrics
  (entity count, components) isolate extraction quality and carry the verdict.

## Next (the fork is now clear)

- **Lean into hybrid** (the working real-corpus path): N=50 confirm, stronger passage embedder (nomic is
  a lower bound), `PASSAGE_K` sweep. Cheap, incremental, real wins.
- **If the strict pure-KG path is still wanted:** the untested lever is an **extraction-architecture**
  change — a dedicated cross-document entity-resolution stage (goldenmatch is literally an entity
  resolver; wiring it as a coref pass over extracted mentions, done RIGHT this time) — not a bigger LLM.
  But cross-doc-link (the embedding version of exactly this) already nulled, so this is a harder,
  lower-confidence bet.

## Lesson

When a quality ceiling looks like "the model isn't smart enough," check whether it's actually an
ARCHITECTURE gap (here: cross-document entity resolution). Scaling the model 4.5× cost ~48 min of A100
and produced a smaller, equally-fragmented graph — the cheap measurement saved a much larger "let's
fine-tune / go frontier" investment that would have chased the wrong variable.
