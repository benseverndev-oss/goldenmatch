# Substrate-Quality Eval — Verdict

**Date:** 2026-06-30
**Branch:** `feat/substrate-quality-eval`
**Spec:** `docs/superpowers/specs/2026-06-30-substrate-quality-eval-design.md`
**Plan:** `docs/superpowers/plans/2026-06-30-substrate-quality-eval.md`
**Run:** Modal `gg-bench` (A10G, Ollama `qwen2.5-7b-instruct` as the extractor), engineered corpus, `--ambiguity 0.0 0.3 0.6`, seed `20260620`. Result: `results/substrate_20_goldengraph-qwen2.5-7b-instruct.md` on volume `gg-bench-cache`.

## What this eval is

The substrate-quality eval scores the **built graph as a knowledge base**, not as a QA engine. It runs two measurements over the same engineered corpus and the same resolver:

- **Level A — resolver in isolation.** Feed the gold mentions' surface forms straight to goldengraph's resolver. This is the resolver's ceiling on a clean record set: no extraction in the loop.
- **Level B — end-to-end build.** Run the full `ingest_corpus` build (7B extracts edges from the rendered text → resolve → durable store), then assign each gold mention to the node it landed in via the doc's `source_refs`. Score that mention→node clustering vs gold.

Both levels reduce to a clustering of gold mentions scored by `metrics.score` (pairwise P/R/F1). Because A and B share the resolver, **the A−B gap isolates EXTRACTION** — the inconsistency the LLM introduces reading real prose — from resolution. Plus two coherence signals on the built graph: connected `components` / `largest-fraction`, and `provenance` (fraction of edges carrying `source_refs`).

## Scoreboard

| ambiguity | ER-F1(A) | ER-F1(B) | A−B gap | components | largest-frac | provenance |
|---|---|---|---|---|---|---|
| 0.0 | 0.9706 | 0.3710 | 0.5995 | 27 | 0.5677 | 1.0000 |
| 0.3 | 0.9034 | 0.2020 | 0.7014 | 64 | 0.0928 | 1.0000 |
| 0.6 | 0.8724 | 0.1375 | 0.7349 | 78 | 0.0524 | 1.0000 |

## Verdict: instrument validated — and it sharpened the story

The plan set two validation clauses. The discriminating one holds; the other was **refuted**, and that refutation is the finding.

**1. The A−B gap widens monotonically as ambiguity rises — CONFIRMED.** `0.60 → 0.70 → 0.73`. Coherence collapses in lockstep: `components 27 → 64 → 78`, `largest-frac 0.57 → 0.09 → 0.05`. As surface variance climbs, the extractor produces a graph that shatters harder. This is exactly the construction ceiling from the QA arc, now reproduced as a monotone number — the instrument does its job.

**2. "A ≈ B ≈ high at ambiguity=0" — REFUTED.** Even on the zero-ambiguity corpus, where every mention uses the canonical surface, the end-to-end build already loses `0.60` F1 (B=0.37 vs A=0.97, 27 components). The fragmentation is **not** primarily an ambiguity problem. There is a large ambiguity-independent floor: the 7B, reading clean canonical text, still emits an inconsistent/incomplete mention set (dropped edges → orphan mentions, cross-doc entity inconsistency). Ambiguity makes it worse (0.60 → 0.73), but ~80% of the damage is already present at ambiguity=0.

**Resolution is not the bottleneck.** Level A stays high across the whole sweep (0.97 → 0.90 → 0.87) — the resolver degrades gracefully with surface variance. The substrate defect lives in **extraction**, which is where the architecture work must land.

## Precision/recall decomposition (v1.1)

The floor is **entirely recall.** Surfacing `P(B)`/`R(B)` on the same corpus:

| ambiguity | ER-F1(B) | P(B) | R(B) | components |
|---|---|---|---|---|
| 0.0 | 0.3629 | 0.9333 | 0.2252 | 28 |
| 0.3 | 0.2020 | 0.9785 | 0.1126 | 64 |
| 0.6 | 0.1375 | 0.9231 | 0.0743 | 78 |

Precision holds at **0.92–0.98** across the whole sweep while recall collapses **0.23 → 0.11 → 0.07.** When the 7B connects two mentions into a node it is almost always right; the damage is connections it never makes — dropped edges and mentions left as orphan singletons. This is **missed-edges, not over-merge**, and it settles the architecture direction: **recall recovery via cross-document entity resolution** (fold the disconnected mentions back into their entities), not splitting. The high precision is headroom — we can merge far more aggressively before precision is at risk.

(Numbers from a second build; ambiguity=0 drifts trivially from the F1-only run above — F1 0.363 vs 0.371, 28 vs 27 components — LLM nondeterminism, within noise.)

## What it means for the roadmap

This is the instrument the reframed north star needed. The QA arc's "the graph shatters on real prose" symptom is now a **standing, optimizable metric** with an attribution axis:

- a large **A−B gap** ⇒ extraction fragmentation (the current dominant term);
- a low **A** itself ⇒ resolution (currently healthy).

The architecture fix — proper cross-document entity resolution using goldenmatch's engine, folding the extractor's inconsistent mentions back into coherent entities — now has a number to move. Target: close the A−B gap at ambiguity=0 first (the constant floor), then hold it flat as ambiguity rises.

## Caveats / v1 scope

- **Precision/recall decomposition — done (v1.1, above).** The floor is recall (P(B) 0.92–0.98, R(B) 0.23→0.07). Confirmed the prior hypothesis: dropped edges → orphan gold mentions, not over-merge.
- **Within-doc over-merge is under-counted.** Alignment keys on the doc's `(subj, obj)`; two gold entities merged inside a single doc can't be distinguished. This does not affect the cross-doc, ambiguity-driven fragmentation that dominates here (documented in the spec).
- **provenance = 1.000 across the board** is expected with a single build engine (every edge that lands carries its `source_refs`); it becomes discriminating only in the multi-engine v2 bake-off.
- Single seed, N=20 questions' worth of corpus (~139 edge-docs / 278 gold mentions). The monotone gap is robust to that, but the absolute floor should be re-confirmed on a second seed before it drives a specific architecture target.

## Next

1. **v1.1 — done.** P/R columns landed; the floor is recall (see decomposition above).
2. **Architecture** — cross-doc entity resolution via the goldenmatch engine to recover the missed edges; re-run this eval as the pass/fail gate. Target: lift R(B) at ambiguity=0 without dropping P(B) below ~0.9.
3. **v2 bake-off** — reframe the multi-engine comparison around substrate quality (this A/B + coherence + temporal as-of correctness), not factoid QA. `provenance` and cross-engine A−B become the discriminators.
