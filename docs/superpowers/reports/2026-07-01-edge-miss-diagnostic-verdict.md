# Edge-Miss Diagnostic — Verdict

**Date:** 2026-07-01
**Branch:** `feat/gliner-recall-probe` (follow-on to the GLiNER probe, same tooling)
**Run:** Modal `gg-bench`, 7B, `--corpus wiki`, best config (`name_ci` + chunking `(6,2)`) + `--gliner-probe`. Leg 90 = fuzzy resolver (default); leg 92 = `GOLDENGRAPH_SUBSTRATE_RESOLVER=exact`.

## What this tested

The GLiNER probe found the real-prose residual is 100% **edge-miss** (33 gold whose entity is in the graph but has no surviving edge), 0% NER-miss. Edge-miss has two possible causes:
1. **Relation-never-extracted** — the 7B produced the entities but no relation connecting them, in any window.
2. **Resolver-dropped self-loop** — the fuzzy resolver merged a doc's src+dst into one node, so `build_batch` dropped the resulting self-loop.

This diagnostic isolates the second with the existing `GOLDENGRAPH_SUBSTRATE_RESOLVER=exact` lever (exact `(name,typ)` resolution → zero within-doc over-merge → zero collapsed self-loops). If edge-miss drops under exact, the resolver was the cause; if flat, the relations were never extracted.

## Result — relation-never-extracted

| leg | resolver | coverage | n_missed | ner_miss | edge_miss |
|---|---|---|---|---|---|
| 90 | fuzzy (default) | 0.4923 | 33 | 0 | 33 |
| 92 | **exact** | 0.4923 | 33 | 0 | **33** |

**Exact resolution recovers zero edges — edge-miss is unchanged at 33.** Perfect within-doc resolution changes nothing, so the missing edges are not resolver-collapsed self-loops. The 7B simply never extracts a relation touching those entities.

### Confirmation the flag took effect (not a no-op)

Byte-identical metrics across two runs warranted a check that `RESOLVER=exact` actually applied. It did: leg 90's captured stdout carries 738 lines of `[controller.run]` / `[score_buckets]` debug from goldenmatch's fuzzy dedupe controller (invoked per-doc during `resolve()`); **leg 92 has none of it**, because `_exact_resolve` bypasses the goldenmatch dedupe path entirely. The absence of that logging is the signature of the exact path running. So the flag threaded, the resolution path genuinely changed, and edge-miss still held at 33 — the result is real, not a caching artifact (the two runs' stdout differ).

## Conclusion

- **The residual is relation-recall-bound, not resolver-bound.** Entity extraction is solved; within-doc resolution is not the bottleneck (fuzzy vs exact is a wash here). The gap is relations the 7B never emits.
- **Edge survival through resolve is NOT a frontier** — there are ~0 resolver-dropped self-loops on this corpus. That candidate sub-project is refuted before being built (the second cheap kill this thread, after GLiNER).
- **Next frontier: relation recall.** The 7B, even over short `(6,2)` windows, extracts entities but omits the relation linking them. Candidate levers (separate sub-project, to be scoped):
  1. **Relation-focused prompting / re-prompt for edges** — after extraction, a second pass asking specifically "what relations hold among these entities?" (the entities are already correct; only the edge is missing).
  2. **REBEL fusion** — the local end-to-end relation extractor already stubbed in `extract_local.rebel_extractor`, fused with the LLM's entities (mirror of the GLiNER-hybrid shape, but for edges — the direction the GLiNER probe pointed at).
  3. **Predicate-vocab / density levers** on the existing extract prompt, measured per-window.

## Method note

Two frontiers (relation recall, edge survival) were on the table after the GLiNER probe. This diagnostic — one Modal leg, zero new code, an existing lever — killed one of them cleanly and pointed all remaining effort at relation recall. Cheap redirection before any build, consistent with the arc.
