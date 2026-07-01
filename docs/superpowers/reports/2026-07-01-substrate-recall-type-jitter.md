# Substrate Recall: The Floor Was Type Jitter, Not Resolution

**Date:** 2026-07-01
**Branch:** `feat/xdoc-type-jitter-fix`
**Follows:** `2026-06-30-substrate-quality-eval.md` (the instrument + the R(B) floor)
**Harness:** Modal `gg-bench` (A10G, `qwen2.5-7b-instruct`), engineered corpus, `erkgbench.run_substrate_eval`.

## The question

The substrate eval put the recall floor at **R(B)=0.23** (ambiguity=0): the same gold entity's mentions across documents rarely land in the same built node. The obvious fix was cross-document entity resolution. This report is the measure-first arc that **refuted that direction** and found the real cause: a one-line key-construction bug.

## The arc — four refutations to one root cause

Every step is a measured Modal run on the substrate eval; each killed a candidate lever.

| # | Experiment | Result | Verdict |
|---|---|---|---|
| 1 | `GOLDENGRAPH_PROFILE_LINK=1` (best cross-doc matcher, fully active — `link calls=139`, fingerprints synthesized) | R(B) 0.23 → 0.23 | **cross-doc ER refuted** |
| 2 | `edge_recall` metric | **0.93** | **extraction drop refuted** — the edges are there |
| 3 | `GOLDENGRAPH_SUBSTRATE_RESOLVER=exact` (no fuzzy over-merge) | R(B) flat, edge_recall 0.93→0.96 | **within-doc over-merge refuted** (real but marginal) |
| 4 | `fragmentation_report` | `type_jitter=0.976`, `name_jitter=0.293`, `identical=0.000` | **root cause: type jitter in the merge key** |

**The root cause.** The durable store unifies entities across documents when their `record_key = record_fingerprint(name, typ)` overlaps. An open-vocab 7B assigns a **different `typ` to the same entity in each document**, so the keys never match and the store never merges:

- `schema matching` → 7 nodes typed `Process` / `Algorithm` / `Algorithm or Technique` / `Data Processing Technique` / `method` / `process`
- `cluster analysis` → `Algorithm` / `Data Analysis Method` / `Data Analysis Technique` / `Method` / `Process` / `Statistical Method`

Each gold entity shattered into **3.49 nodes** on average (max 7); 41/45 entities fragmented. `identical=0.000` confirms the store itself is correct — every non-merge is explained by name/type variation, not a merge bug.

## The fix

Stop keying cross-document merge on the extractor's per-document type. `resolve._key_payload` gains a gated `GOLDENGRAPH_XDOC_KEY`:

- `name` — key on name only (type-agnostic)
- `name_ci` — key on the case-folded name (also absorbs case-only name jitter)
- unset (default) — today's `(name, typ)` behavior, unchanged

This is the entity analog of the predicate `SCHEMA_CANON` that won the QA arc: a deterministic, free, source-side constraint. **Default-off**, gated.

## Validation — full ambiguity sweep (`GOLDENGRAPH_XDOC_KEY=name_ci`)

| ambiguity | R(B) base → name_ci | ER-F1(B) base → name_ci | P(B) name_ci | A−B gap base → name_ci | components base → name_ci |
|---|---|---|---|---|---|
| 0.0 | 0.2314 → **0.7475** (3.2×) | 0.371 → **0.832** | 0.9379 | 0.60 → **0.14** | 27 → **1** |
| 0.3 | 0.1126 → **0.4282** (3.8×) | 0.202 → **0.589** | 0.9454 | 0.70 → **0.31** | 64 → **7** |
| 0.6 | 0.0743 → **0.3502** (4.7×) | 0.138 → **0.503** | 0.8899 | 0.73 → **0.37** | 78 → **4** |

Recall multiplies at every level. **Precision holds** (0.89–0.95 — no observed cost on this corpus). At ambiguity=0 the graph collapses from 27 fragments into **one connected component** — an actually-coherent knowledge base — and `mean_nodes_per_entity` drops 3.49 → 1.33.

## The two-layer architecture (empirically grounded)

The residual A−B gap **grows with ambiguity** (0.14 → 0.31 → 0.37):

1. **`name_ci` kills the type-jitter floor** — the dominant term, and the *whole* story at ambiguity=0.
2. **The growing residual at higher ambiguity is genuine surface variance** (the corpus's variant surfaces). This is the one place real cross-doc fuzzy/embedding linking can finally earn its keep — now that `name_ci` cleared the type-jitter noise that was drowning it. The profile-link null (step 1) was measured on the *raw* build; **re-measuring profile-link layered on `name_ci` is the natural follow-on**, not a v1 requirement.

The remaining ambiguity=0 residual (9/45 entities, `name_jitter=1.0`) is pure name-level extraction noise: predicate-bleed into names (`authored string metric`) and garbage predicate-as-entity nodes (`acquired`/`Verb`). A separate extraction-cleanup follow-on.

## Decision & scope

- **v1: ship `name_ci` gated, default-off** (`GOLDENGRAPH_XDOC_KEY`). Proven, zero measured precision cost. Measure across corpora before any default flip.
- **Type-canonicalization variant** (coarse closed type vocab, keyed `(name_ci, coarse_type)`) is the homograph-safe follow-on before flipping a default — `name_ci` drops type entirely, which risks merging homographs (`Apple` company vs fruit) on general corpora this concept-corpus doesn't exercise.
- **Eval hardening:** `edge_recall` + `fragmentation_report` are now permanent substrate-eval instruments (they found this; they gate future work).
- **Gate target:** R(B)@ambiguity=0 ≥ 0.75, P(B) ≥ 0.90.

## Lesson

Task structure / deterministic constraint beats clever methods — a third time (after predicate `SCHEMA_CANON` and the refuted open-vocab clustering). We came one approval away from building a global two-phase resolution engine to fix what turned out to be a 15-line key-construction bug. The measure-first arc — refute, don't assume — is what caught it.
