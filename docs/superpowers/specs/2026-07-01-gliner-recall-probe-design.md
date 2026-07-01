# GLiNER Entity-Recall Probe — Design

**Date:** 2026-07-01
**Branch:** `feat/gliner-recall-probe` (off `main`)
**Program:** goldengraph substrate-quality arc — a **measure-first gate** for the GLiNER-hybrid extraction-recall lever. This sub-project does NOT build the hybrid; it decides whether the hybrid is worth building.

## Problem

Chunking (WIN, #1350) lifted real-prose substrate coverage to ~0.508 on the wiki corpus. The residual ~0.49 is gold that never aligned. **But an unaligned gold mention (`node_of < 0`) has two distinct causes** (`substrate_eval.py` `edge_recall` :235-248 and the KNOWN-LIMIT note :42-45):

- **NER-miss** — the entity was never extracted, so no node exists for it anywhere in the graph. *This is what GLiNER can close.*
- **Edge-miss** — the entity *was* extracted (a node exists) but its doc produced no surviving edge (the LLM dropped the relation, or the fuzzy resolver collapsed src+dst into a dropped self-loop), so it isn't in the aligner's edge-derived candidate set for that doc. **GLiNER surfacing the same entity changes nothing here** — the failure is a lost *edge*, not a missing entity.

GLiNER is a discriminative NER encoder (no generative density limit) and is the candidate lever for the **NER-miss** subset only. It helps iff two things hold:

1. **(a) GLiNER actually surfaces the missed entities** — the ones the LLM pipeline fails to align.
2. **(b) those entities can be given edges** — the substrate aligner's candidate node set is built **only from edges** (`substrate_eval.py:120-123`: `by_doc` collects `subj`/`obj` of edges). An **edgeless node can never align**, so a naive "add GLiNER entities as nodes" union moves coverage by exactly zero. Any real hybrid must make GLiNER's entities edge-participating (the LLM must relate them).

Building the linking hybrid (b) is only justified if (a) is true **and the residual is actually NER-miss, not edge-miss**. The recall-prompt lever taught us to **measure before building**. So this sub-project measures the NER-addressable share of the residual and GLiNER's recall over it, and gates the hybrid on that.

## Goal

A measurement-only probe that, in one Modal run on the wiki corpus, reports **how much of the residual gold GLiNER would recover**, plus a noise proxy. No production engine change; the gate stays off. The output is a single verdict: PASS (build the hybrid) or REFUTED (stop).

## Non-goals

- **Not the hybrid.** No relation-linking, no prompt change, no `ingest` change. That is a separate, conditional sub-project designed only if this probe PASSES.
- No new GLiNER wiring beyond the existing `extract_local.gliner_extractor` (reused as-is, or called directly for entity spans).
- No change to the substrate metric or the aligner — the probe *reuses* the aligner's match logic so its numbers are apples-to-apples.

## Architecture

Two units: a **pure scorer** (box-testable) and an **impure runner** (Modal).

### Data flow

```
wiki corpus (19 docs, 65 gold, aliases)
 ├─ build best-config graph (name_ci + chunking (6,2))     [existing run_wiki path]
 │     └─ _assign_real_nodes_aliased -> node_of: which gold ALIGNED (>=0) vs MISSED (<0)
 ├─ GLiNER per-doc -> {base_doc_id: set(entity surfaces)}   (see seq-length caveat)
 └─ gliner_probe_report(graph, gold, aliases, gliner_by_doc)   [PURE, unit-tested]
        -> { gliner_recall, llm_coverage, n_gold, n_missed,
             n_ner_miss, n_edge_miss, ner_recovered_frac,
             residual_recovered_frac, junk_rate }
```

### Unit 1 — pure scorer `substrate_eval.gliner_probe_report(...)`

Signature: `gliner_probe_report(graph, gold_mentions, qid_aliases, gliner_by_doc) -> dict`.

- `node_of = _assign_real_nodes_aliased(graph, gold_mentions, qid_aliases)` — reuse the exact aligner, so "LLM missed this gold" means the same thing the coverage metric means. `missed` = the golds with `node_of < 0`.
- **NER-miss vs edge-miss split.** For each missed gold, check whether **any** graph entity anywhere (over `id2surf` built exactly as the aligner builds it, `substrate_eval.py:113-119` — `surface_names` + `canonical_name`, case-folded) matches its alias set. If **no** node matches → **NER-miss** (no entity exists; GLiNER-addressable). If some node matches but it wasn't an in-doc edge candidate → **edge-miss** (entity exists, edge lost; GLiNER can't help). This split is the correction that keeps the headline number honest.
- **Match rule (shared helper, no drift).** A gold mention `(qid, surface, doc)` is **GLiNER-matched** iff some GLiNER surface *in the same doc* (`gliner_by_doc` keyed by `_base_doc_id(doc)`, mirroring the aligner's `_base_doc_id` keying) matches its alias set, using the aligner's rule: `match = aliases[qid] | {surface.strip().lower()}`; a GLiNER surface `g` matches iff `g_lc in match` OR substring-either-way (`g_lc in m or m in g_lc`) for some `m in match`, where **`g_lc = g.strip().lower()`**. GLiNER emits surfaces un-lowercased (`extract_local.py:129`), and the alias/gold sets are fully case-folded (`wiki_corpus.py:38`, `substrate_eval.py:115-127`), so the helper MUST case-fold the GLiNER surface or every cased entity silently fails to match (a false REFUTED). The same helper backs both the aligner's node-surface match and the probe's GLiNER-surface match — same rule, different inputs.
- Metrics:
  - `gliner_recall` = |gold GLiNER-matched| / |gold| — GLiNER's raw NER recall ceiling.
  - `llm_coverage` = |node_of ≥ 0| / |gold| — the current pipeline baseline (should ≈ 0.508).
  - `n_missed`, `n_ner_miss`, `n_edge_miss` — the residual and its two-way split (`n_missed = n_ner_miss + n_edge_miss`).
  - **`ner_recovered_frac` = |NER-miss ∩ GLiNER-matched| / |NER-miss|** — **the true prize**: of the entities that are genuinely absent (GLiNER-addressable), what share GLiNER surfaces. This, not the conflated residual, is the gate number.
  - `residual_recovered_frac` = |missed ∩ GLiNER-matched| / |missed| — reported for context, but explicitly flagged as conflating NER-miss and edge-miss (overstates the prize).
  - `junk_rate` = |GLiNER surfaces matching NO gold| / |all GLiNER surfaces| — a *rough* noise proxy, read with the gold-incompleteness caveat below.

Pure (no GLiNER call, no I/O) → unit-tested on the box with hand-built `graph` / `gold` / `gliner_by_doc`. All ratios guard their denominators: `|gold|=0`, `|missed|=0`, `|NER-miss|=0`, and empty `gliner_by_doc` each yield `0.0` for the dependent metric, never a `ZeroDivisionError`.

### Unit 2 — impure runner `run_substrate_eval.run_wiki_gliner_probe()`

`run_wiki` today returns a metrics-only dict — it does not expose `graph`/`gold`/`aliases`. So first factor its build into a tiny shared helper `_wiki_build() -> (documents, gold, qid_aliases, graph)` that both `run_wiki` and the probe call (no duplicated `load_wiki_corpus()` + `_build_graph_from_documents()`). Then:

- `run_wiki_gliner_probe()` calls `_wiki_build()` (inheriting the best config from env: `GOLDENGRAPH_XDOC_KEY=name_ci`, `GOLDENGRAPH_CHUNK_EXTRACT=1`, `(6,2)`).
- Loads GLiNER via the existing `extract_local.gliner_extractor` (or `GLiNER.from_pretrained` for raw spans), runs it **per-doc**, builds `gliner_by_doc` keyed by `_base_doc_id(doc)`.
- Calls the pure scorer, prints one `[gliner-probe] ...` line + a markdown block (same persistence path as `run_wiki`).
- **One switch, not two:** a single `--gliner-probe` CLI flag on `main` (and an env alias `GOLDENGRAPH_GLINER_PROBE=1` so the Modal `--opts` mechanism can set it, matching the chunking legs) routes `--corpus wiki` to `run_wiki_gliner_probe()` instead of `run_wiki()`. GLiNER labels/threshold: the existing recall-friendly defaults (`person/organization/location/work/event/date`, threshold 0.4); the probe also reads threshold 0.3 as a second point since it is a recall ceiling.

### Infra — Modal image

Add `gliner` to `modal_bench.py`'s `pip_install` list (currently absent). The `urchade/gliner_mediumv2.1` model downloads from HF at first run; acceptable for a one-off probe.

## The falsifiable gate

The gate reads **`ner_recovered_frac`** (the true, un-conflated prize) together with `n_ner_miss / n_missed` (is the residual even NER-addressable?) and `junk_rate`:

| Outcome | Signature | Action |
|---|---|---|
| **PASS** | The residual is substantially NER-miss (`n_ner_miss / n_missed` non-trivial) **and** GLiNER recovers a material share of it (`ner_recovered_frac` clearly non-trivial, e.g. ≳ 0.25) | Build the GLiNER-hybrid sub-project (GLiNER entities → LLM relation prompt so they get edges). |
| **REFUTED** | Residual is mostly edge-miss (GLiNER can't help), **or** GLiNER barely finds the NER-miss gold (`ner_recovered_frac` ≈ 0) | Stop. The residual is not an NER-recall gap GLiNER can close; record the negative. |

The exact PASS threshold is a judgment call surfaced in the verdict, not hard-coded. `gliner_recall`, `llm_coverage`, and `residual_recovered_frac` are context.

**Two things the gate does NOT prove (fold into the verdict, not the code):**
1. **Necessity, not sufficiency.** PASS means GLiNER *surfaces* the missed entities — a *necessary* condition. The hybrid's actual value additionally requires the LLM to *relate* those entities so they earn an edge (the aligner's requirement). "GLiNER finds it but the hybrid still can't edge it" is an un-probed failure mode that carries into the hybrid sub-project as its central risk.
2. **`junk_rate` over-reads as precision cost.** Gold is only the 65 *wikilinked* mentions; a real lead names many legitimate non-wikilinked entities that GLiNER will correctly surface, all of which land in the `junk` numerator. So a high `junk_rate` partly measures "gold covers only wikilinks," not "GLiNER hallucinates." Read it as a loose upper bound on noise, and say so.

## Error handling

- GLiNER load / predict failure → the runner logs and emits an empty `gliner_by_doc` (probe reports zeros, not a crash) — the run still yields the LLM baseline.
- The pure scorer is total: empty `gliner_by_doc` → GLiNER metrics `0` (guard the `/0`); empty gold → `n_gold=0`, all ratios `0`; `|missed|=0` (all-aligned, `llm_coverage=1.0`) → `residual_recovered_frac=0`; `|NER-miss|=0` → `ner_recovered_frac=0`. Every ratio guards its own denominator.
- **GLiNER sequence-length truncation (bias caveat, not a bug):** `gliner_mediumv2.1` has a bounded input window (a few hundred tokens); a ~20-sentence lead run per-doc may truncate and drop tail entities, *undercounting* `gliner_recall`/`ner_recovered_frac`. The bias is conservative (toward REFUTED), so a PASS is trustworthy; a REFUTED should be sanity-checked with a chunked-GLiNER pass (run GLiNER over the same `(6,2)` windows and union) before believing it. Note this in the verdict.

## Testing

Pure scorer, box-safe (no GLiNER, no network), in `tests/test_substrate_eval.py`:
1. **NER-miss vs edge-miss split** — build a graph with: (a) an aligned gold (its node has an in-doc edge), (b) an **edge-miss** gold (a node whose surfaces match the alias set EXISTS but participates in no edge for that doc → `node_of<0` yet not NER-addressable), (c) a **NER-miss** gold (no node anywhere matches). Assert `n_edge_miss=1`, `n_ner_miss=1`. Then a `gliner_by_doc` that surfaces both the edge-miss and NER-miss golds → `ner_recovered_frac = 1/1` (only the NER-miss counts as the prize) while `residual_recovered_frac = 2/2` (context, conflated) — the test that proves the correction matters.
2. **Case-folding** — a GLiNER surface emitted cased (`Barack Obama`) matches a lowercased alias/gold set; assert it counts (guards the false-REFUTED bug).
3. **Match parity with the aligner** — matches via alias (`Big Blue` vs gold `IBM`, `IBM` in aliases) and via substring (`Nabbes` vs `Thomas Nabbes`) count; an unrelated surface does not; matching is **per-doc** (a GLiNER surface in doc A does not match a gold in doc B).
4. **Junk rate** — GLiNER surfaces matching no gold raise `junk_rate`; all-matching → 0.
5. **Degenerate guards** — empty `gliner_by_doc` → GLiNER metrics 0; empty gold → `n_gold=0`; **all-aligned graph (`|missed|=0`) → `residual_recovered_frac=0` and `ner_recovered_frac=0`, no divide-by-zero.**

## Rollout

Measurement artifact only. Emits `docs/superpowers/reports/2026-07-01-gliner-recall-probe-verdict.md` with the numbers and the PASS/REFUTED call. Nothing ships to the engine. If PASS, the verdict hands off to the GLiNER-hybrid spec; if REFUTED, the extraction-recall thread closes at chunking and the arc moves to a different frontier (e.g. cross-corpus robustness of the name_ci + chunking stack).
