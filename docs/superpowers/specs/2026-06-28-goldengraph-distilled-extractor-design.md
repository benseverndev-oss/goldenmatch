# GoldenGraph local-OSS-LLM KG/RAG quality -- investigation record + verdict

> **CONCLUDED 2026-06-29.** A measure-first arc took the local 7B lane from **0.15 -> 0.672**
> end-to-end answer-match, all free (no paid models, no training). The winning lever was the
> cheapest: **schema-constrained + direction-canonical ingest** (deterministic post-processing), which
> **beats a 32B OSS model (0.569)** at the same task. Three plausible "smarter model" levers were each
> refuted by measurement before any was shipped. Fine-tuning is NOT worth it for this task. See
> [[project_goldengraph_local_oss_llm_lane]].

**Status:** CONCLUDED -- schema-constrained deterministic path adopted; distillation/fine-tune closed.
**Date:** 2026-06-29 (investigation 2026-06-28..29)
**Owner:** Ben Severn
**Harness:** `scripts/distill/modal_bench.py` (Modal GPU eval), `scripts/distill/modal_train.py` (QLoRA)

## The arc (engineered corpus, qwen2.5:7b-instruct, N=60, ambiguity 0.0, eval seed 20260620)

| stage | answer-match | what changed |
|-------|-------------|--------------|
| naive ball synthesis (`mode=local`) | 0.241 | seed a ball, let the LLM synthesize over it |
| relation-guided walk (`trace_chain`) | 0.293 | follow the named relations LLM-free instead of synthesizing |
| + under-merge bridge | 0.586 | re-seed each hop by canonical name (cross the split bridge entity) |
| **+ schema-constrained + direction-canonical ingest** | **0.672** | closed-vocab predicate snapping + passive/inverse direction flip at ingest |

`synthesis_given_gold = 1.00` and `retrieval_coverage = 1.00` throughout: given the *correct* graph the
engine is perfect, so the entire 1.00 -> 0.67 gap is **graph-construction error** (extraction +
resolution), not retrieval or synthesis. Every lever above repairs a graph defect; the schema-constrained
one repairs it at the source (deterministically, free).

## The three refutations (why "use a smarter model" was wrong each time)

1. **Distillation-as-first-instinct -- refuted.** The original premise (below) said synthesis was the
   bottleneck; localization showed `synthesis_given_gold=1.00`. The bottleneck is **extraction +
   resolution fidelity**, not model reasoning.
2. **Scale the model -- refuted.** A **32B** OSS teacher (same family) scored **0.569 <= the 7B's
   0.586** on the identical pipeline -- *worse*, with more hop-1 deaths and 2 unseeded anchors. The
   defects (under-merge, reversed edges, predicate noise) are open-extraction *task-framing* failures,
   not capacity failures; a bigger model makes the same class of errors with different surface variance.
3. **Fine-tune the direction -- refuted.** A QLoRA on the same 7B to teach canonical edge direction on
   reverse-phrased text scored `reverse_direction_acc` **0.000** (iter 1: r16/3ep) -> **0.078** (iter 2:
   r64/8ep, 50%-reverse data, MLP targets). 4x the reverse data + 4x rank + 2.6x epochs moved it from 0%
   to ~8% -- the base "subj = first-mentioned entity" prior is too strong to train out, and the fine-tune's
   predicate accuracy (0.82) is *worse* than the free deterministic vocab-snapping (~1.0 on in-vocab). The
   merged model therefore cannot beat the 0.672 schema-constrained 7B at e2e (bake-off: 0.672 vs
   `__FINETUNE_E2E__`).

**Verdict:** the deterministic, free, schema-constrained path is the answer. Fine-tuning is closed for
this task. The harness (`modal_train.py` QLoRA + `gen_gold_pairs.py` key-free gold labels) is kept,
working, and documented for a future task where a model-quality gap is actually shown.

---

## Historical design (SUPERSEDED -- kept for the record)

The sections below are the original "distill the SYNTHESIS model" design, written when synthesis was
*believed* to be the bottleneck. The measurements above superseded it. Preserved so the reasoning trail
is auditable.

### Measured finding that drove the (then) pivot

The local OSS-LLM lane runs `qwen2.5:7b-instruct` end-to-end at answer-match **0.25** (decay
`{1:0.5, 2:0.5, 3:0.0, 4:0.0}`). The original design assumed EXTRACTION was the bottleneck. The
extraction-F1-in-isolation eval (`erkgbench.qa_e2e.extraction_eval`, 127 edge-docs vs planted gold)
**refuted that** (run 28324290271):

| config | entity-F1 | relation-F1 | parse-fail |
|--------|-----------|-------------|------------|
| qwen + JSON-mode | 0.916 | 0.811 | 1/127 |
| qwen, no JSON | 0.914 | 0.808 | 1/127 |
| REBEL | 0.706 | 0.551 | 0/127 |

The 7B EXTRACTS well (0.92 entity / 0.81 edge). JSON-mode is a no-op (the model already emits valid
JSON); REBEL is worse. So the right entities + edges ARE in the graph -- the 0.25 answer-match gap is
**downstream: multi-hop SYNTHESIS** (tracing the relation chain from the subgraph to the answer), which
is a reasoning skill a 7B is weak at and gpt-4o-mini is better at. Hence the pivot: distill SYNTHESIS,
not extraction.

(Caveat the eval can't see: it scores edge EXISTENCE, predicate-label-agnostic. If the 7B mislabels
predicates, relation-filtered traversal breaks even at high edge-F1 -- Stage 0 below disambiguates.)

## Goal

A small student that, given `(question, subgraph)`, produces the correct multi-hop-traced answer --
distilled from gpt-4o-mini -- lifting the local lane's multi-hop answer-match while staying CPU-
inferable + key-free. Synthesis student = a decoder LLM (LoRA on qwen); REBEL/seq2seq is OUT (it's a
relation extractor, irrelevant to synthesis).

## Stage 0 -- CONFIRM the bottleneck before building (cheap, gating)

Do NOT train until synthesis is confirmed as the gap. Two cheap isolation measurements:

1. **synthesis-given-gold** (the scorecard's existing row): hand the model the GOLD subgraph + the
   question, score answer-match for {base-7B, gpt-4o-mini}. If the 7B is LOW here while gpt-4o-mini is
   high -> synthesis IS the bottleneck (build proceeds). If the 7B is already HIGH given the gold
   subgraph -> the gap is RETRIEVAL (the right subgraph isn't reaching synthesis), and we pivot AGAIN to
   retrieval, not synthesis distillation.
2. **predicate-aware extraction-F1** (extend `extraction_eval`): rules out the "edges right, predicates
   wrong" caveat. If predicate-F1 is much lower than edge-F1, some gap is extraction-predicate after
   all.

Both reuse existing harnesses (the scorecard `synthesis_given_gold` + `extraction_eval`); wiring Ollama
into the scorecard job is the only new plumbing. **Stage 0 is the gate: synthesis distillation proceeds
only if (1) shows the 7B weak at synthesis-given-gold.**

## Stages (given Stage 0 confirms synthesis)

### Stage 1 -- Capture synthesis training pairs

The supervision target is `(question, subgraph) -> answer`. Unlike extraction, the LABELS are
INDEPENDENT of the teacher: the engineered/MuSiQue corpora ship GOLD answers, and the scorecard's
`build_gold_subgraph` already builds the gold subgraph per question. So each training example is
`(question, formatted_gold_subgraph) -> gold_answer`, optionally enriched with a gpt-4o-mini REASONING
TRACE (the relation-tracing chain) as the target to teach HOW, not just WHAT. Capture over engineered +
MuSiQue. Teacher cost = a gpt-4o-mini pass to produce traces (the answers themselves are free corpus
gold). Output `synth_pairs.jsonl`.

### Stage 2 -- Dataset build

`(prompt = synthesis prompt with question + formatted subgraph) -> (target = traced answer)`.
Document/question-disjoint train/val/heldout split (deterministic hash, no leakage). Reuse
`scripts/distill/build_dataset.py` (the split logic is task-agnostic; only the record shape changes).

### Stage 3 -- Train on Modal (LoRA only)

LoRA/QLoRA on `qwen2.5-3b`/`7b-instruct` over the synthesis pairs, via the existing Modal harness
(`scripts/distill/modal_train.py` -- the `lora` path; the `rebel`/seq2seq path is unused for synthesis).
Modal creds in Infisical (`MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET`, project a99885f0-…, env `dev`). To
avoid forgetting extraction (the student still does both in the pipeline), MIX a slice of extraction
pairs into the LoRA data, or keep extraction on the base behavior (LoRA is additive). A few GPU-hours.

### Stage 4 -- Serve

The synthesis student IS the `local_llm` (goldengraph uses one LLM for extraction + synthesis; the
student is fine at extraction already and now better at synthesis). Publish the merged GGUF as a GitHub
Release / HF repo; the eval lane `ollama create` + `local_llm=<student>`. (A separate synthesis-only
model injected just for `synthesize_local` is possible but adds an injection seam -- defer.)

### Stage 5 -- Eval / gate

1. **synthesis-given-gold (PRIMARY, isolation):** {base-7B, student, gpt-4o-mini} answer-match given the
   GOLD subgraph. The student must close most of the base->teacher gap. Cheap, low-noise (per-question
   over the corpus), the iteration signal.
2. **End-to-end answer-match + decay curve:** the full local lane with the student -> does the multi-hop
   tail (2-3 hop) actually lift?
3. **Held-out:** train on diverse questions; eval synthesis-given-gold on held-out engineered + MuSiQue.
   A win only on the tiny engineered corpus is NOT a win.

## Honest constraints

- **Reasoning is harder to distill than format.** Extraction (the original target) is a structured
  format task small models learn well; multi-hop synthesis is a REASONING task -- a 3-7B LoRA may close
  some of the gap but 3-4-hop tracing could stay weak. Lower confidence of success than an extraction
  distill would have had. Stage 0 + the synthesis-given-gold gate keep us honest.
- **Labels are independent** (corpus gold answers), so no teacher label-cap -- the ceiling is the corpus
  gold, and the teacher only contributes reasoning traces.
- **One model does both** extraction (already good) + synthesis (the target); LoRA is additive, mix
  extraction pairs to avoid forgetting.
- Non-gating research lane; deterministic gates stay the blocking signal.

## What carries over from the (refuted) extraction design

- `scripts/distill/` scaffold: `modal_train.py` (the `lora` path), `build_dataset.py` (task-agnostic
  split), README/Infisical-Modal auth. `capture_pairs.py` (DISTILL_LOG reader) is EXTRACTION-specific
  and is superseded by a synthesis-pair capture (Stage 1).
- `extraction_eval.py` stays as the instrument that DROVE this pivot (and Stage 0's predicate-aware
  variant); the synthesis eval is the scorecard's `synthesis_given_gold` row, surfaced as a CLI/lane.
- Modal infra (resolved), the eval discipline (independent labels, disjoint held-out), the publish->lane
  artifact seam -- all unchanged.

## Open decisions (for Ben)

1. **Run Stage 0 first?** Strongly recommended -- it's cheap (reuses synthesis_given_gold) and could
   pivot us again to RETRIEVAL. Build only after it confirms synthesis.
2. **Student size:** qwen-3B (cheaper) vs 7B (the one we measured; better base reasoning) for the LoRA.
3. **Trace-distillation vs direct:** teacher reasoning traces as the target (teaches tracing) vs direct
   `subgraph->answer` SFT (simpler). Traces likely help multi-hop but cost a teacher pass.
