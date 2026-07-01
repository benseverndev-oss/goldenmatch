# Real-Corpus (Wikidata) Substrate Validation — Design

**Date:** 2026-07-01
**Status:** Design (approved for spec)
**Follows:** the substrate-eval arc — type-jitter fix `GOLDENGRAPH_XDOC_KEY=name_ci` (#1331, R(B) 0.23→0.75) and the homograph variant (#1335/#1336/#1337). All of it was measured on the **synthetic engineered corpus**.

## Problem

Every substrate finding this arc — the R(B)=0.23 floor, the type-jitter root cause, the name_ci 0.23→0.75 win — rides on one synthetic corpus built from ER/dedup **concepts** (`Levenshtein distance`, `blocking`, …). Two things make that a risk worth retiring first:

1. **Unvalidated on real entities.** The type-jitter magnitude and the name_ci win may be a property of the engineered rendering, not real prose/entities.
2. **The concept corpus is plausibly a WORST case.** Its entities are all abstract and effectively one type, so the 7B has maximal room to jitter. Real entities (people, organizations, places) have **crisp, stable types**, where the 7B may jitter far less — meaning the engineered corpus could have *overstated* the whole problem.

This validates the fix on **real entities** with real types and real aliases, reusing the existing eval.

## Approach (decided)

Reuse `dataset/records.csv` (built by `dataset/build_real.py`): **48 distinct real entities** across three sources — Wikidata (`Q37156`), RxNorm drug brand/generic (`rxcui:11289`, e.g. Coumadin/warfarin), and events (slug ids like `fifa-world-cup-2018`). Each entity has real surface variants (`IBM` / `International Business Machines Corporation` / `IBM Corp.`), real `entity_type`, real `context`, and `entity_id` (its QID / rxcui / slug) as ground truth. **The `entity_id` is NOT always a QID — group on it verbatim, never assume a `Q` prefix (that would drop 24 of 48 entities).** Feed these to the **existing engineered generator** as the entity source; everything downstream is reused. The alternatives (real Wikipedia prose + string-match gold; an off-the-shelf entity-linking dataset) are recorded under Deferred — this is the low-friction, highest-signal first cut.

## Architecture

`generate_engineered` gains a gated entity source. When `GOLDENGRAPH_BENCH_ENTITIES=real`, entities come from `records.csv` (QID id, real label canonical, real aliases as `variants`) instead of `concepts.jsonl`. The synthetic typed-edge graph, ambiguity-dialed rendering, and `emit_gold_mentions` are **entity-source-agnostic** and reused verbatim. The ambiguity dial now draws variant surfaces from *real* aliases. `run_substrate_eval` runs unchanged (the gate is env, threaded through Modal `--opts`).

## Components

### 1. `_load_real_entities()` (engineered.py)
- Read `dataset/records.csv`; group rows by `entity_id` **verbatim** (may be a QID, `rxcui:<n>`, or a slug — do NOT assume a `Q` prefix).
- Per entity → `_Entity(id=entity_id, canonical=<primary label>, variants=<other distinct mentions>)`. Primary label = the mention with the lowest `record_id`, sorted **numerically** (`record_id` is an int stored as string — a lexical sort would put `"10"` before `"2"`). Variants = the remaining distinct `mention` strings (real aliases).
- Anchored to `__file__` (CWD differs local vs Modal). Pure / no network (`records.csv` is committed).

### 2. Gate in `generate_engineered`
- At entity load: `entities = _load_real_entities() if os.environ.get("GOLDENGRAPH_BENCH_ENTITIES","").strip().lower()=="real" else _load_entities()`. Default (`concepts.jsonl`) unchanged. Mirrors the existing `GOLDENGRAPH_BENCH_COOCCUR` / `_HOMOGRAPH` env-gate pattern.

### 3. Real surface variance (free)
- `_render_mention` already picks a random `variant` at rate `ambiguity`. With real entities, variants are real aliases, so the ambiguity sweep exercises **real** surface variation (abbreviations, full names, exonyms) instead of synthetic corruptions.

### 4. Gold + eval (unchanged)
- `emit_gold_mentions` derives `(entity_id, surface, doc_id)` off the generated docs; the real id (QID / rxcui / slug) flows through as `entity_id`. `run_substrate_eval` + `score_substrate` need no change.

## Data flow / what it tests

Rendered doc: `"{surface_of_QID_a} {rel} {surface_of_QID_b}."` (synthetic edge over real entities). The 7B extracts + types each real entity; the store keys on `record_key`. Two questions:

1. **Does `name_ci` still beat the `(name,typ)` baseline on real entities?** (does the fix generalize)
2. **Is the `(name,typ)` baseline R(B) HIGHER here than the engineered 0.23?** (are real crisp types less jitter-prone — did the concept corpus overstate the problem?)

## Validation

Substrate eval on the real-entity corpus, `GOLDENGRAPH_XDOC_KEY` ∈ {unset baseline, `name_ci`}, ambiguity sweep {0.0, 0.3, 0.6} (real aliases). Report the R(B)/P(B)/gap curve beside the engineered numbers.

Reading the outcome (all are informative, none is a pass/fail — this is a *calibration*, not a gate):
- **baseline ≈ 0.23, name_ci ≈ 0.75** → type-jitter is universal; the engineered corpus was representative; the arc's conclusions hold on real entities.
- **baseline ≫ 0.23 (e.g. 0.5+), name_ci smaller win** → real crisp types jitter less; the concept corpus overstated the problem; name_ci still helps but the headline number was domain-specific.
- **name_ci ≤ baseline** → the fix is a concept-corpus artifact (would be a genuine surprise; would re-open the whole finding).

## Scope

**v1:** the real-entity loader + gate + one calibration run. **Deferred:**
- **Real-homograph validation of `name_ci_type`** — `records.csv` has real same-surface/distinct-id homographs (`Georgia` → Q230 country vs Q1428 US state, `failure_class=same_name_collision`); a natural next test of the homograph work on real data.
- **Real Wikidata edges** (vs synthetic edge-gen over real entities) — the sourced QIDs aren't interconnected, so v1 keeps synthetic edges.
- **Real Wikipedia prose** (level-2 realism) — real sentences/ambiguity; the explicitly set-aside deeper validation.

## File plan

- `benchmarks/.../qa_e2e/engineered.py` — `_load_real_entities()`; the `GOLDENGRAPH_BENCH_ENTITIES=real` gate in `generate_engineered`.
- Test: `benchmarks/.../tests/test_real_entities.py` — `_load_real_entities` groups records.csv by `entity_id` (verbatim: QID/rxcui/slug) into `_Entity`s (correct id/canonical/aliases); the gate switches the source; `emit_gold_mentions` yields real-id gold.

## Testing

Box-safe: `_load_real_entities` returns 48 `_Entity`s keyed on the verbatim `entity_id` (assert a mix — a `Q…`, an `rxcui:…`, and a slug are all present, NOT filtered to `Q`-prefix), with real aliases as variants; an entity with multiple mentions (e.g. Q37156) has ≥2 variants; the gate makes `generate_engineered` emit real-id-keyed gold mentions. One Modal substrate run for the calibration table.

## Risks

- **Synthetic edges are nonsensical on real entities** (`IBM {rel} NATO`) — acceptable: the substrate eval scores entity *co-reference* (mention→node via edge endpoints), not relation semantics, so semantically-empty edges still exercise the resolution path. Documented, user-accepted.
- **48 entities is small** — a signal, not a leaderboard; enough to calibrate the baseline-vs-name_ci delta. Expandable via `sources.jsonl` later.
- **Not real prose** — validates real entities/types/aliases, NOT real sentence structure; the honest boundary, and the reason level-2 (real Wikipedia prose) stays a named follow-on.
- **Canonical-label choice is heuristic** (lowest `record_id`) — at ambiguity=0 the canonical is used throughout, so the choice shifts which real string is "canonical" but not the resolution logic; deterministic, low-stakes.
