# GoldenGraph scorecard — real-LLM rows (Phase 2 of slice A)

## Context

Slice A's deterministic core (PR #1274) shipped two scorecard stages — **resolution**
(ER-F1, reused from er-kg-bench) and **retrieval** (bridge-recall under the 4-dial ER
ablation, the `(ER)^hops` proof, deterministic + CI-gated). The measured curve
confirmed the thesis (oracle 1.00 → goldengraph 0.56 → name_only/none 0.23, gap
widening 0.44→0.91 across hops).

Two scorecard stages remain, both needing a real LLM, plus a validation of the
free bridge-recall proxy:

- **extraction** (entity-F1 / relation-F1 of extracted vs gold triples) — localizes
  *where* the graph-only 0.22 ceiling loses facts (the binding constraint the whole
  program is pointed at).
- **synthesis** (answer-match given the *gold* subgraph) — the synthesis ceiling:
  can the LLM read a correct graph.
- **answer-match confirmation** (the 4-dial ablation measuring real-LLM answer-match
  instead of bridge-recall) — validates that bridge-recall is a faithful proxy for
  answer quality.

This spec is **Phase 2 of slice A**. It does not touch the deterministic gate. The
remaining slices (B aggregation/temporal, C crossover, D KG-vs-KG) stay separate.

## Gating posture

Real-LLM → costs money, non-deterministic → **never a hard gate**. This is an
**opt-in `workflow_dispatch` lane**, budget-capped via the bench's existing
`BudgetTracker(BudgetConfig(max_cost_usd=...))` + `record_usage`, producing a
`SCORECARD.md` artifact. Assertions render as PASS/WARN in the report; they do not
fail the process. The #1274 bridge-recall gate stays the blocking signal.

All three rows run on the **engineered** corpus (the only corpus with gold triples +
gold chains) and reuse #1274: `gold.py` (`GoldGraph`, `gold_chain`), `dials.py` (the
four `*_keys` + `surface_to_canon`), `ablation.py` (`_build_store`), `scorecard.py`
(`bridge_recall`), `metrics.py` (`answer_match`), and the engine adapter's
`_CountingLLM` + real `OpenAIClient`.

## Metric definitions

### extraction-F1 (real `_extract`, no store)

For each engineered edge document, gold = `{src_surface, dst_surface}` (from #1274's
`Document.src_surface`/`dst_surface`) + the relation between them. Run real
`_extract(doc.text, llm)` → `Extraction(mentions, relationships)` and score, micro
across the corpus:

- **entity-F1:** extracted mention surfaces vs the gold surface pair, normalized
  (lowercase / strip via `metrics._normalize`). Micro TP/FP/FN.
- **relation-F1 (existence-based):** a relationship is correct if its subj/obj
  mention surfaces match the gold src/dst pair (either direction — edges are walked
  both ways), **ignoring the predicate label**. The LLM emits free-form predicates
  ("works at" vs gold `works_at`) and synthesis treats labels as hints, so "did it
  recover the *edge*" is the honest signal. Micro TP/FP/FN.

Low entity-F1 → the LLM misses entities; low relation-F1 → misses edges. This is the
localizer for the 0.22 ceiling. Cost: N extraction calls.

### synthesis-given-gold (real `synthesize_local`, no store, no retrieval)

Build the gold chain subgraph from `gold.py`:
`{entities:[{entity_id, canonical_name, typ}], edges:[{subj, predicate, obj}]}` over
the chain's canonical entities + gold edges. Call
`synthesize_local(question, gold_subgraph, llm, seed_names=[start_canonical])` and
score `answer_match(pred, gold_answer)`. Mean by hop = the synthesis ceiling given a
perfect graph. `synthesize_local` is entity-only, which fits: engineered gold answers
*are* canonical entity names. Cost: N synthesis calls.

### answer-match ablation — 4-dial, matched to bridge-recall

Per dial (`oracle`/`goldengraph`/`name_only`/`none`), reuse `ablation._build_store`
(oracle extraction, dial record_keys) + the coverage map + oracle-seed +
`_retrieve_local` ball — **identical to the bridge-recall measurement**. The only new
step: real `synthesize_local` over that ball (seed_names = the seed node's
canonical_name), scored with `answer_match` vs `gold_answer`. Recompute `bridge_recall`
on the same ball so the report carries both curves side by side. Cost: 4×N synthesis
calls (builds are LLM-free — oracle extraction).

**Tracking verdict (PASS/WARN, not gating):** the answer-match dial ordering matches
the bridge-recall ordering (`oracle ≥ goldengraph ≥ name_only ≥ none`, tolerance). A
faithful proxy means they track. *Expected honest effect:* under the weak dials the
answer entity is reached under a variant surface, so the model may output the variant
rather than the canonical gold → answer-match drops further, tracking bridge-recall
*more* strongly. That is a real ER→answer effect (bad resolution costs you the
canonical name), not a metric artifact — noted in the report. A genuine **divergence**
(bridge-recall high for a dial but answer-match low) is the finding the row exists to
surface: synthesis failing to read a ball that contains the answer.

Total LLM cost ≈ **6N calls** (N extract + N synth + 4N synth), budget-capped.

## Components / files

New, under `erkgbench/qa_e2e/`:

- **`scorecard_llm.py`** — `extraction_f1(gold_triples, extraction)`,
  `build_gold_subgraph(gold_chain)`, `synthesis_given_gold(question, gold_chain, llm)`,
  `answer_match_ablation(corpus, llm, ...)` (reuses `ablation._build_store` +
  `bridge_recall`), `tracking_verdict(answer_match_by_dial, bridge_recall_by_dial)`,
  a `ScorecardResult` dataclass, and `render_scorecard_md`.
- **`run_scorecard.py`** — CLI (real LLM, `--budget-usd`, `--seed`/`--n-questions`/
  `--ambiguity`), writes `SCORECARD.md`. Stops cleanly when the budget cap is hit
  (records partial results + a BUDGET-EXHAUSTED note).

CI: a new opt-in `workflow_dispatch` lane (a job in `bench-graphrag-qa.yml` or a
sibling) with a `budget_usd` input + `OPENAI_API_KEY`; uploads `SCORECARD.md`. Never
`ci-required`.

## Testing

Pure offline (no LLM/network; the ablation store-build row `importorskip`s the wheel):

1. **`extraction_f1`** — synthetic gold + synthetic `Extraction`: perfect match → 1.0;
   a missing entity drops recall; a spurious entity drops precision; an edge in either
   direction between the right surfaces counts as a relation TP regardless of predicate
   label.
2. **`synthesis_given_gold`** — `StubLLM` returning `"...\nAnswer: X"`: assert the gold
   subgraph is constructed (the synthesis prompt carries the chain entities/edges) and
   `answer_match` is scored against the parsed answer.
3. **`tracking_verdict`** — synthetic per-dial answer-match + bridge-recall dicts: PASS
   when the orders match, WARN on a divergence.
4. **Budget cap** — a fake LLM + a tiny `max_cost_usd`: the runner stops and records
   `budget_exhausted` rather than running all 6N calls.
5. **Report render** — `render_scorecard_md(ScorecardResult)` carries the three stage
   sections + both ablation curves + the verdict line.
6. **e2e ablation row** — `importorskip("goldengraph_native")`; validates in the opt-in
   lane.

## Scope guard (YAGNI)

Engineered corpus only. No new corpus, no musique extraction-F1 (no gold triples
there), no LLM-judge variant (use the deterministic `answer_match`; `judge_prompt`
exists but is out of scope here). The #1274 deterministic gate is untouched; this lane
never blocks merge. Slices B/C/D remain separate specs.
