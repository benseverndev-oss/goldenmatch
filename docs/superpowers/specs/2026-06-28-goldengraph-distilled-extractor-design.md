# GoldenGraph distilled extractor -- purpose-built small KG model design

**Status:** design
**Date:** 2026-06-28
**Owner:** Ben Severn
**Worktree:** TBD (design only -- no implementation in this doc; training is off-GH GPU)

## Problem

The local OSS-LLM lane works end-to-end ($0, no key) but `qwen2.5:7b-instruct` scores answer-match
**0.25** with a decay curve `{1:0.5, 2:0.5, 3:0.0, 4:0.0}` -- it collapses on multi-hop. The pipeline is
`text -> extract -> resolve -> retrieve -> synthesize`; resolution is goldenmatch (model-independent),
so the OSS loss is in **extraction** (missing/wrong triples -> broken chains -> compounding `^hops`
decay) and, secondarily, synthesis. A small general model is weak at the structured extraction task. The
goal: a **purpose-built small model distilled from gpt-4o-mini** for goldengraph's extraction, lifting
OSS-local quality toward the teacher while staying CPU-inferable and key-free at run time.

## Goal

A repeatable distillation pipeline -- capture teacher labels -> fine-tune a small student -> serve it
through an existing extractor seam -> measure extraction-F1 in isolation + end-to-end answer-match --
that produces an extractor whose extraction-F1 materially beats the base OSS model and approaches the
gpt-4o-mini teacher, validated on held-out data (not the tiny engineered corpus).

**Scope: EXTRACTION first** (highest ROI). Synthesis distillation is a documented Phase 2.

## Why extraction first (ROI)

- **Narrow, structured task** -- the sweet spot for small-model fine-tuning (REBEL itself is a
  fine-tuned seq2seq relation extractor, and it's ALREADY a wired extractor option here).
- **Free teacher labels** -- `GOLDENGRAPH_DISTILL_LOG` already captures `(text -> extraction)` pairs
  from any run; a gpt-4o-mini pass yields the training set with no new capture code.
- **Measurable in isolation** -- the scorecard's `extraction_counts` already computes entity-F1 +
  relation-F1, so each training iteration is evaluated WITHOUT a full pipeline run.
- **Existing serve seam** -- `_resolve_extractor()` (`GOLDENGRAPH_EXTRACTOR=api|rebel|gliner`) already
  swaps the extractor; a fine-tuned model plugs in here with no engine change.
- **It gates everything** -- a missing edge can't be recovered by retrieval or synthesis, and multi-hop
  compounds it. Fixing extraction is the unblock for the decay curve.

## Architecture (4 stages + eval)

```
[teacher capture]      gpt-4o-mini + GOLDENGRAPH_DISTILL_LOG over diverse text
   |  (text, extraction_json) pairs
[dataset build]        dedupe + filter empties + train/val/heldout split (disjoint docs)
   |  extractor_train.jsonl / val.jsonl / heldout.jsonl
[student train]        OFF-GH GPU: LoRA/QLoRA(qwen2.5-3B)  OR  seq2seq fine-tune (REBEL/T5)
   |  adapter / merged GGUF / checkpoint
[serve]                Ollama custom model (path a)  OR  GOLDENGRAPH_EXTRACTOR=<finetuned> (path b)
   |
[eval/gate]            extraction-F1 isolation (scorecard) + e2e answer-match (local lane) on HELDOUT
```

### Stage 1 -- Teacher-label capture (data generation)

Run the teacher (`gpt-4o-mini`, the measured ceiling) over a DIVERSE text corpus with
`GOLDENGRAPH_DISTILL_LOG=<path>` set. `_DistillLogger` appends `(text -> extraction)` JSONL. Sources:
MuSiQue paragraphs (realistic) + engineered docs + a broader open text set (e.g. Wikipedia paragraphs)
to avoid overfitting to the bench's narrow domain. Use the SAME extraction prompt + JSON-mode the
student will run, so the target format matches exactly. The capture can piggy-back on the existing
gpt-4o-mini bench dispatch (DISTILL_LOG is already a workflow knob) OR a dedicated capture run. Output
a clean `pairs.jsonl`. HONEST: teacher quality CAPS the student -- gpt-4o-mini extraction is itself
imperfect (the measured ceiling on this bench, not ground truth), so the student aims to MATCH the
teacher cheaply, not beat it. One-time, budget-capped OpenAI cost (the thing we're working around --
but offline + amortized, not per-eval).

**Dataset-size targets (interacts with open decision #3):** a seq2seq specialist (path b) trains well
on ~2-10k pairs; a LoRA-3B (path a) wants more (~10-50k) to avoid catastrophic-forgetting artifacts.
Size the teacher pass to the chosen student; start small (~2-5k) to validate the loop before a large
capture.

### Stage 2 -- Dataset build

`build_dataset.py`: load `pairs.jsonl`, drop empty/parse-failed extractions, dedup near-identical docs,
normalize to the student's target schema, and split into train/val/**heldout** by DOCUMENT (disjoint --
no doc leakage). Keep a held-out slice from BOTH MuSiQue and engineered so eval covers realistic + the
bench domain. Emit counts + a schema report (predicate vocabulary coverage).

### Stage 3 -- Student fine-tune (OFF-GH GPU)

Two student options (the spec recommends evaluating both, leading with whichever the A/B favors):

- **(a) Decoder LLM -- LoRA/QLoRA on `qwen2.5-3b-instruct`.** Train to emit the extraction JSON given
  the prompt. Serve via Ollama (merge adapter -> GGUF -> `ollama create`), set `local_llm=<custom>` in
  the lane -- SAME inference path, no engine change. More general (also reusable for synthesis later).
- **(b) Seq2seq specialist -- fine-tune `Babelscape/rebel-large` (or a small T5) on `(text -> triples)`
  in REBEL's `<triplet>` format on OUR predicate schema.** Tiny, fast, CPU-only, no LLM serving; plugs
  via the EXISTING `GOLDENGRAPH_EXTRACTOR=rebel` seam (`rebel_extractor(model=<our-checkpoint>)` already
  takes a model arg). Cheapest to train + serve; the risk is REBEL's format/vocab vs our relations
  (the A/B arm B measures base-REBEL today to size this).

Training infra: **Modal** (creds in Infisical: `MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET`, project
`a99885f0-c5af-4ae1-9dc8-255cc60aa129` env `dev`). A GPU is NOT free on GH -- Modal is serverless GPU:
define training as a `@app.function(gpu="A10G"|"A100")` Python function, mount the dataset, `modal run`,
write the artifact to a Modal Volume / publish out. LoRA-3B or seq2seq need only a few GPU-hours. One-
time per dataset revision; no VM to manage. (Resolves open decision #2.)

### Stage 4 -- Serve (+ the off-GH -> on-GH artifact seam)

The trained artifact lives off-GH; the eval lane runs on-GH, so the artifact must land somewhere the
runner pulls from. **Publish it as a GitHub Release asset** (the `bench-dataset-v1` release pattern
already used here) or an HF Hub repo, then the eval lane downloads it in a setup step:

- Path (a) LoRA-qwen: publish the merged GGUF + a `Modelfile`; eval step `gh release download ... &&
  ollama create gg-extract -f Modelfile` -> `local_llm=gg-extract`.
- Path (b) seq2seq: publish the checkpoint (Release/HF); eval step downloads it; `GOLDENGRAPH_EXTRACTOR=
  rebel` + a new `GG_REBEL_MODEL=<downloaded path or HF id>` env (extend `rebel_extractor` to read the
  model from env -- a small code change, in scope when this is built).

### Stage 5 -- Eval / gate (the honest measurement)

**Eval gold is INDEPENDENT of the teacher (avoids circularity).** Teacher labels are TRAINING-only. The
headline extraction-F1 is scored against the bench's **planted gold triples** -- the engineered corpus
emits `src::rel::dst` document ids, and `scorecard_llm.extraction_counts(gold_src, gold_dst, extraction)`
already scores entity/relation-F1 against those, NOT against teacher output. So {base-OSS, student,
teacher} are all scored vs the SAME independent planted gold; "approaches the teacher" means "closes the
gap to the teacher's F1-vs-gold," not "agrees with the teacher's labels." Teacher-agreement (student vs
teacher labels) is reported SEPARATELY as a distillation-fidelity sanity check, never the headline -- a
student that perfectly mimics teacher ERRORS would score high on agreement but NOT on F1-vs-gold.

1. **Extraction-F1 in isolation (PRIMARY):** `extraction_counts` -> entity-F1 + relation-F1 of
   {base-OSS, student, teacher} vs planted gold on the HELDOUT engineered docs. Student must beat
   base-OSS by a frozen margin and close most of the gap to the teacher. Cheap (no full pipeline) ->
   the iteration signal.
2. **End-to-end answer-match (where there is no triple-gold):** MuSiQue paragraphs ship NO
   schema-triples, so they can't score extraction-F1 -- their signal is the local-lane ANSWER-MATCH
   (student-extraction + OSS-synthesis): does the better graph lift the decay curve (esp. 2-3 hop)?
3. **Held-out discipline:** train on teacher pairs over DIVERSE text; eval extraction-F1 on a held-out
   ENGINEERED slice (the only source of planted triples) + answer-match on held-out MuSiQue (realistic).
   The 45-entity engineered universe is tiny -> a win only there is NOT a win; MuSiQue answer-match is
   the generalization check. (Generating schema-triple gold for MuSiQue is a larger future asset.)

## Components / file structure (when implemented -- NOT in this design)

- `scripts/distill/capture_pairs.py` -- wrap a teacher DISTILL_LOG run into clean `pairs.jsonl`.
- `scripts/distill/build_dataset.py` -- dedupe/filter/split + schema-coverage report.
- `scripts/distill/train_extractor.py` -- LoRA (path a) or seq2seq (path b) trainer; runs off-GH GPU.
- `scripts/distill/eval_extractor.py` -- extraction-F1 (reuse the scorecard counts) on heldout, {base,
  student, teacher}.
- serve artifact: an Ollama `Modelfile` (a) or a checkpoint + a `GG_REBEL_MODEL` env in
  `extract_local.rebel_extractor` (b).
- `.github/workflows/`: the EVAL of a trained student reuses `bench-graphrag-qa` (local_llm /
  GOLDENGRAPH_EXTRACTOR via `opts`); only TRAINING is off-GH.

## Error handling / honesty

- Teacher cap: student <= gpt-4o-mini extraction quality (stated; the win is cost, not a new ceiling).
- Overfit: diverse training text + disjoint heldout (MuSiQue-led) is mandatory; a win only on the
  engineered corpus is NOT a win.
- Predicate schema: our relations vs REBEL/teacher vocab -- the dataset's schema-coverage report flags
  mismatch; fine-tune on OUR schema.
- Reasoning depth: extraction fixes the graph, NOT synthesis's multi-hop reasoning -- 3-4 hop may need
  Phase 2 (synthesis distillation) even with perfect extraction.
- Non-gating: this is a research/eval lane, never a blocking CI gate (non-deterministic training).

## Open decisions (for Ben)

1. **Student type:** lead with seq2seq-REBEL (cheapest, CPU-only, seam exists) or LoRA-qwen-3B (more
   general, reusable for synthesis)? The A/B arm-B (base REBEL) result should inform this. **Default if
   inconclusive:** start with seq2seq-REBEL -- cheapest to train + serve and the seam already exists, so
   it validates the whole loop at lowest cost before committing to the heavier LoRA path.
2. ~~**Training infra:**~~ RESOLVED -- **Modal** (serverless GPU; creds in Infisical, see Stage 3).
3. **Teacher-pass budget:** how many docs to capture (dataset size vs OpenAI cost), and whether to
   piggy-back capture on the existing gpt-4o-mini bench runs vs a dedicated pass.
4. **Phase 2 (synthesis distillation):** in-scope now or only after extraction is shown to close the
   gap?

## Sequencing (this design's place)

This design is the BLUEPRINT; the cheap-win A/B (JSON-mode extraction / REBEL / hybrid, dispatched
2026-06-28) measures where the loss actually is FIRST. If the A/B shows extraction is the bottleneck
(REBEL or json-mode clearly helps), this distillation pipeline is the next build; if hybrid retrieval
recovers most of the loss, extraction distillation drops in priority. Decide after the A/B numbers land.
