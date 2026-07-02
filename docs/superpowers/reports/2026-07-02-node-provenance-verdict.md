# Node Provenance — Verdict

**Date:** 2026-07-02
**Branch:** `feat/node-provenance`
**Spec/Plan:** `docs/superpowers/specs/2026-07-02-node-provenance-design.md` · `docs/superpowers/plans/2026-07-02-node-provenance.md`
**Run:** Modal `gg-bench`, `--corpus wiki`, best config (`name_ci` + chunking `(6,2)`, SCHEMA_CANON off). 7B seeded + DeepSeek-V3.

## What this built

`source_refs` on entity nodes end-to-end (build_batch → core store accretive union → `as_of`→`EntityNode` → native `graph_view_to_dict` → per-doc aligner union), so the shipped aligner reaches per-doc-relationless entities (present in the graph, no surviving edge in a given doc). The presence-aligner probe (PR #1367) greenlit it, having recovered coverage 0.49→1.0 at P(B)=1.0 — *globally*.

## Result — modest, safe WIN (not the coverage→1.0 the probe implied)

Read on **DeepSeek-V3** (near-deterministic; the 7B seed-42 numbers wobbled ±0.09 R(B) across the wheel-rebuild legs and are too noisy for a clean delta — a secondary finding, below):

| axis | V3 before fix | V3 after fix | Δ |
|---|---|---|---|
| coverage | 0.4923 | 0.5077 | +1 gold mention |
| R(B) | 0.3990 | **0.4394** | +0.040 |
| F1(B) | 0.5704 | **0.6105** | +0.040 |
| P(B) | 1.0000 | 1.0000 | held |

The fix landed and is a genuine improvement — **but coverage barely moved (+1 mention), not toward ~1.0.** The gain is in *clustering quality* (R(B)/F1), not raw coverage.

## Why the probe over-promised, and the honest refinement

The presence probe recovered coverage to 1.0 via a **global** surface match (reach any node whose surface matches, ignoring doc). This engine fix is **per-doc**: a node is a candidate in doc D only if its `source_refs` contain D. The two differ exactly on the residual's composition:

- **~1 of the 33 residual gold was truly in-doc-relationless** — extracted in its own doc (so `source_refs` contains that doc) but with no surviving edge there. Node provenance reaches it → the +1 coverage.
- **~32 are cross-doc same-surface** — the gold surface in doc A matches an entity extracted *only in other docs* (its `source_refs` are {B, C…}, not A). Per-doc provenance correctly does **not** reach these; only the global match does — and global conflates same-surface distinct entities, a precision risk that merely happened to not bite on this low-ambiguity 19-doc corpus (P held 1.0 there, not guaranteed elsewhere).

So the presence-probe verdict's "the ~51% coverage ceiling is a **pure** metric artifact, recoverable at zero precision cost" was **true only for the *global* aligner**. The safe, general, per-doc fix recovers a small part; **most of the coverage gap is a cross-doc coreference/surface problem, not an in-doc edge problem.** That is the real, corrected shape of the ceiling. The R(B)/F1 gain is node provenance doing its legitimate job — correctly attributing some entities to their docs improves which cross-doc-merged node a gold aligns to.

## The plumbing bug (and a hard-won lesson)

The first three measurement legs showed a **flat no-op** (coverage 0.4923, `entities_with_source_refs=0/269`). I mis-diagnosed it as a stale Modal wheel and spent ~6 legs on cache-busting (clear volume, `cargo clean`, `--force-reinstall`, `cache.reload`) — none fixed it. **Building the wheel locally found the real bug in one shot:** `entities()` carried `source_refs` but `query()` (the path the bench uses) did not. Cause: the Task-2 `replace_all` edit matched only the 12-space-indented `entities()` dict; `graph_view_to_dict`'s 8-space entity loop was **skipped**, and a `grep -c "source_refs"` of 2 (the second hit was the *pre-existing edge* serialization) falsely confirmed both sites. Lesson: **`replace_all` on indentation-sensitive code silently under-matches — verify each site, and a whole-pipeline round-trip beats a symbol grep.** The wheel was never stale; the bench-caching detour chased a phantom. (The rebuild-hardening it produced — `force-reinstall` + `cargo clean` + `cache.reload` — is kept as legitimate protection for future Rust iterations.)

Also surfaced: **7B seed-42 reproducibility is weaker across wheel rebuilds than the seed-spike verdict implied** (R(B) 0.21–0.30 over legs 160–167). The seed pins a single wheel's decoding; a rebuilt wheel (accumulated main drift + GPU float non-determinism) reintroduces variance. V3's near-determinism made it the trustworthy read here.

## Decision

- **Ship it.** Node provenance is the *correct* model (entities are doc-attributed, a genuine substrate capability — you can now list a doc's entities including relationless ones), the R(B)/F1 gain is real and clean on V3 at P=1.0, and it is fully back-compat (serde-default + aligner union). Default-on correctness fix.
- **Correct the record:** the coverage ceiling is **not** a pure, safely-recoverable metric artifact. The bulk is cross-doc surface coreference — recoverable only by a *global* (precision-risky) match, not the safe per-doc path. The presence-probe verdict's headline is amended accordingly.
- **PyPI `goldengraph-native` republish** is a separate optional rollout (the bench builds from source).

## Honest caveats

- **One corpus, small N; 7B noisy.** The clean signal is V3-only (+0.04 R(B)/F1). Treat it as "a real, modest win," not a precise effect size.
- **Coverage is still ~0.51.** The engine fix does not move the headline coverage number materially; the substrate's *relational* quality improved, its *reach* did not.

## Follow-ons

1. **Metric-reporting split** (the deferred sibling sub-project): report entity-presence coverage vs relational R(B) separately — now doubly warranted, since this fix moves R(B) but not coverage.
2. **Cross-doc surface coreference** is the real remaining coverage lever (and the precision-risk it carries) — a distinct, harder problem than node provenance, if pursued.
