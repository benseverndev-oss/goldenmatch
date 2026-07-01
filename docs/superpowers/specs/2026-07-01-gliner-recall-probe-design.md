# GLiNER Entity-Recall Probe — Design

**Date:** 2026-07-01
**Branch:** `feat/gliner-recall-probe` (off `main`)
**Program:** goldengraph substrate-quality arc — a **measure-first gate** for the GLiNER-hybrid extraction-recall lever. This sub-project does NOT build the hybrid; it decides whether the hybrid is worth building.

## Problem

Chunking (WIN, #1350) lifted real-prose substrate coverage to ~0.508 on the wiki corpus. The residual ~0.49 is **entities the generative 7B never emits in any window** — a pure NER-recall gap. GLiNER (a discriminative NER encoder, no generative density limit) is the candidate next lever. But it only helps if two things hold:

1. **(a) GLiNER actually surfaces the missed entities** — the ones the LLM pipeline fails to align.
2. **(b) those entities can be given edges** — the substrate aligner's candidate node set is built **only from edges** (`substrate_eval.py:120-123`: `by_doc` collects `subj`/`obj` of edges). An **edgeless node can never align**, so a naive "add GLiNER entities as nodes" union moves coverage by exactly zero. Any real hybrid must make GLiNER's entities edge-participating (the LLM must relate them).

Building the linking hybrid (b) is only justified if (a) is true. The recall-prompt lever taught us to **measure before building**. So this sub-project measures (a) — GLiNER's incremental entity recall — and gates the hybrid on it.

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
 ├─ GLiNER per-doc (whole lead; encoder, no density limit) -> {doc_id: set(entity surfaces)}
 └─ gliner_probe_report(graph, gold, aliases, gliner_by_doc)   [PURE, unit-tested]
        -> { gliner_recall, llm_coverage, incremental_recall,
             residual_recovered_frac, junk_rate, n_gold, n_missed }
```

### Unit 1 — pure scorer `substrate_eval.gliner_probe_report(...)`

Signature: `gliner_probe_report(graph, gold_mentions, qid_aliases, gliner_by_doc) -> dict`.

- `node_of = _assign_real_nodes_aliased(graph, gold_mentions, qid_aliases)` — reuse the exact aligner, so "LLM missed this gold" means the same thing the coverage metric means.
- A gold mention `(qid, surface, doc)` is **GLiNER-matched** iff some GLiNER surface in that doc matches its alias set, using the **same match rule as the aligner** (`match = aliases[qid] | {surface_lc}`; a GLiNER surface `g` matches iff `g in match` OR substring-either-way `g in m or m in g` for some `m in match`). This is factored into a shared helper so the aligner and the probe cannot drift.
- Metrics:
  - `gliner_recall` = |gold GLiNER-matched| / |gold| — GLiNER's raw NER recall ceiling.
  - `llm_coverage` = |node_of ≥ 0| / |gold| — the current pipeline baseline (should ≈ 0.508).
  - `incremental_recall` = |gold with node_of < 0 AND GLiNER-matched| / |gold| — the prize, as a share of ALL gold.
  - `residual_recovered_frac` = |missed ∩ GLiNER-matched| / |missed| — the prize as a share of the *residual* (more interpretable: "GLiNER recovers X% of what the LLM missed").
  - `junk_rate` = |GLiNER surfaces matching NO gold| / |all GLiNER surfaces| — a noise proxy for the fragmentation/precision cost the hybrid would inherit.

Pure (no GLiNER call, no I/O) → unit-tested on the box with hand-built `graph` / `gold` / `gliner_by_doc`.

### Unit 2 — impure runner `run_substrate_eval.run_wiki_gliner_probe()`

- Reuses `run_wiki`'s build (so it inherits the best config from env: `GOLDENGRAPH_XDOC_KEY=name_ci`, `GOLDENGRAPH_CHUNK_EXTRACT=1`, `(6,2)`).
- Loads GLiNER via the existing `extract_local.gliner_extractor` (or `GLiNER.from_pretrained` directly for raw spans), runs it **per-doc on the whole lead** (the NER upper bound; GLiNER has no density limit), builds `gliner_by_doc`.
- Calls the pure scorer, prints one `[gliner-probe] ...` line + a markdown block (same persistence path as `run_wiki`).
- Selected by `GOLDENGRAPH_GLINER_PROBE=1` inside `run_wiki` (env switch, so the Modal invocation matches the chunking legs — just add the env). GLiNER labels/threshold: the existing recall-friendly defaults (`person/organization/location/work/event/date`, threshold 0.4); the probe may also try threshold 0.3 as a second reading since it is a recall ceiling.

### Infra — Modal image

Add `gliner` to `modal_bench.py`'s `pip_install` list (currently absent). The `urchade/gliner_mediumv2.1` model downloads from HF at first run; acceptable for a one-off probe.

## The falsifiable gate

Read `residual_recovered_frac` and `junk_rate` together:

| Outcome | Signature | Action |
|---|---|---|
| **PASS** | GLiNER recovers a material share of the residual (`residual_recovered_frac` clearly non-trivial, e.g. ≳ 0.25) at a tolerable `junk_rate` | Build the GLiNER-hybrid sub-project (GLiNER entities → LLM relation prompt so they get edges). |
| **REFUTED** | GLiNER barely finds the missed gold (`residual_recovered_frac` ≈ 0), or only by drowning in junk (`junk_rate` high) | Stop. The residual is not an NER-recall gap GLiNER can close; record the negative. |

The exact PASS threshold is a judgment call surfaced in the verdict, not hard-coded — the point is to see the number before committing to build. `gliner_recall` and `llm_coverage` are reported as context (is GLiNER even competitive with the LLM on entities it *does* share?).

## Error handling

- GLiNER load / predict failure → the runner logs and emits an empty `gliner_by_doc` (probe reports zeros, not a crash) — the run still yields the LLM baseline.
- The pure scorer is total: empty `gliner_by_doc` → `gliner_recall=0`, `incremental_recall=0`, `junk_rate=0` (guard the `/0`); empty gold → all-zeros with `n_gold=0`.

## Testing

Pure scorer, box-safe (no GLiNER, no network), in `tests/test_substrate_eval.py`:
1. **Incremental recall** — a graph where some gold aligns (has edges) and some doesn't; a `gliner_by_doc` that covers one missed gold and one already-aligned gold → assert `incremental_recall` counts only the missed one, `residual_recovered_frac` = 1/|missed_here|.
2. **Match parity with the aligner** — a GLiNER surface that matches only via alias (e.g. `Big Blue` vs gold `IBM` with `IBM` in aliases) counts as matched; a substring case (`Nabbes` vs `Thomas Nabbes`) counts; an unrelated surface does not.
3. **Junk rate** — GLiNER surfaces matching no gold raise `junk_rate`; all-matching → 0.
4. **Degenerate guards** — empty `gliner_by_doc` → all GLiNER metrics 0, no divide-by-zero; empty gold → `n_gold=0`, no crash.

## Rollout

Measurement artifact only. Emits `docs/superpowers/reports/2026-07-01-gliner-recall-probe-verdict.md` with the numbers and the PASS/REFUTED call. Nothing ships to the engine. If PASS, the verdict hands off to the GLiNER-hybrid spec; if REFUTED, the extraction-recall thread closes at chunking and the arc moves to a different frontier (e.g. cross-corpus robustness of the name_ci + chunking stack).
