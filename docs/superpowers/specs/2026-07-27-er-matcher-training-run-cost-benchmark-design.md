# ER-Matcher Training Run + Cost Benchmark (Sub-project 2) — Design

**Date:** 2026-07-27
**Status:** Design (approved for spec review)
**Scope:** Sub-project 2 of the production-grade OSS ER-matcher effort — the gated,
cost-optimized training run that produces a merged LoRA model on the real corpus.

## Context

Phase 1a (sub-project 1) shipped the multi-source data pipeline (#2195), and the
real corpus is now built: **~25,000 pairs** (train 17,690 / val 3,796 / test 3,503,
~52% match) from FEBRL + the 4 Leipzig CC-BY benchmarks (see `fetch_data.py`, #2200).

The training infra (`scripts/er_matcher/{train,_train_runtime,modal_train,perf_report}.py`)
exists but has never run the full training end-to-end on real data, and has gaps:
- `learning_curve` is always `[]` — the "sweep driver" `perf_report` references
  does not exist, so the gate's "would more data help?" check is uninformed.
- The config (`config.yaml`) is sized for the old ~2,844-row synthetic default.
- No checkpoint/resume — a preempted/failed paid run restarts from scratch.
- The perf gate reports a single config; it can't compare cost/quality across
  training configs or pick the cheapest adequate GPU tier.

**Dependency:** the smoke-fix PR **#2178** (`processing_class=`→`tokenizer=`,
flash-attn wheel, `rich`, gradient checkpointing, pynvml catch) must land first —
`main`'s train infra is otherwise broken. SP2 branches off `main` after #2178.

## Goal

Produce a trained LoRA ER-matcher (Qwen2.5-3B) on the real 25k corpus via a
**data-driven, cost-gated** run: benchmark two training configs, let the human pick
from an A/B cost-vs-quality scorecard, then run the winner to a merged model + a
basic held-out eval.

Non-goals (separate sub-projects):
- Sub-project 3 — the cross-benchmark eval harness (SP2 does only a basic held-out
  test eval; per-benchmark match-F1/calibration parity is SP3).
- Sub-project 4 — release (quantize, publish, model card).
- Phase 1b — the rich synthetic generator.

## Why not "rewrite the trainer in Rust"

The smoke run measured **99.3% GPU utilization** — training is GPU-FLOP-bound, not
host-bound. The compute is the 3B model's forward+backward executed by
cuBLAS/cuDNN/FlashAttention-2 (already-optimal CUDA). A host-language rewrite would
optimize the ~0.7% that isn't the GPU and change zero GPU FLOPs, so it cannot reduce
training cost. (Rust *is* the reference for the CPU-bound ER **scoring** kernels —
a different regime.) Cost is reduced by ML levers instead: quantization (QLoRA),
cheaper GPU tier, fewer epochs — which is what the benchmark below measures.

## Design

### 1. End-to-end flow

```
measure seq_len (real corpus, P95 cap 1024)
      -> BENCHMARK PHASE (autonomous, soft ~$8 ceiling):
           for config in {bf16-LoRA, QLoRA-4bit}:
             smoke + learning-curve sweep (10/25/50/100% slices)
             -> measure peak_mem (=> cheapest GPU tier it fits: L4/A10G/A100),
                tokens/s (=> $/step), eval_loss per slice (=> quality + slope)
      -> A/B SCORECARD -> HUMAN picks the config
      -> FULL RUN on the chosen config (checkpoint/resume)
      -> merged LoRA model on the volume + basic held-out eval
```

### 2. Learning-curve sweep driver

New `train_sweep` path: in ONE container (single base-model load), train short runs
on 10/25/50/100% train-data slices with eval enabled, collecting `{frac, eval_loss}`
per slice into `learning_curve`. `perf_report.learning_curve_slope` then makes the
gate's "still climbing?" check honest. One container + sequential sub-trainings
(cheap) over 4 parallel containers (4x model-load cost) — the sequential design is
the default.

### 3. Config-matrix (bf16 vs QLoRA)

The benchmark runs the smoke+sweep for BOTH `qlora_4bit: false` (bf16-LoRA) and
`qlora_4bit: true` (QLoRA-4bit). Each emits its own metrics (peak mem, tokens/s,
learning curve). The 4-bit path shrinks the base weights ~6GB→~1.5GB, typically
fitting a cheaper GPU tier at a small quality cost — the benchmark measures whether
that trade is worth it rather than assuming it.

### 4. `perf_report` extension: cost-tier selection + A/B compare

- **Tier selection (pure):** given measured `peak_mem_gb`, pick the cheapest GPU
  tier whose capacity fits (L4 24GB ~$0.80/hr, A10G 22GB ~$1.10/hr, A100-40GB
  ~$2.10/hr, A100-80GB ~$2.50/hr — a table, rates are config, not hard-coded).
  Extrapolate full-run $ on that tier.
- **A/B compare (pure):** given two configs' metrics, emit a scorecard row per
  config — fits-tier, extrapolated full-run $/wall, learning-curve slope,
  final-slice eval_loss — for the human decision. No auto-pick; the human decides.

### 5. Config right-sizing + checkpoint/resume

- `seq_len` stays MEASURED (P95 over the real corpus, cap 1024) — real product/
  citation strings are longer than the synthetic 384; the smoke reports the true
  value. Epochs default 2; the sweep informs whether that's right.
- Checkpoint: `save_strategy="steps"` + `save_steps` to the persisted volume;
  `resume_from_checkpoint` so a preempted/failed run resumes. Robustness for the
  paid full run.

### 6. Guardrails, auth, autonomy

- The benchmark phase runs **autonomously** under a soft **~$8** ceiling (surface to
  the human if a run's extrapolation would exceed it before launching).
- The **full-run** config is the human's explicit pick at the A/B scorecard — so the
  larger spend always has a human in the loop; no hard autonomous full-run cap
  needed.
- Auth uses the on-disk `benzsevern` Modal token. The leaked token
  (`ak-yj6kcd...`) stays revoke-pending and is never used.

## Testing

Pure decision logic is box-safe unit-tested (no GPU, no network), matching Phase 1a:
- sweep aggregation (`{frac, eval_loss}` → curve), `learning_curve_slope`
- cost-tier selection (measured mem → cheapest fitting tier + $ extrapolation)
- A/B scorecard construction
- config-matrix expansion (bf16/QLoRA), config validation

The GPU code paths (`_train_runtime` sweep/QLoRA branches, `modal_train` entrypoints)
are exercised by the actual Modal runs (the execute step), not CI — same boundary as
the existing trainer.

## Components / file map (indicative — finalized in planning)

- `scripts/er_matcher/_train_runtime.py` — add the sweep loop + QLoRA branch +
  checkpoint/resume (extends existing).
- `scripts/er_matcher/modal_train.py` — add `train_sweep` entrypoint + config-matrix
  driver + GPU-tier parametrization.
- `scripts/er_matcher/perf_report.py` — add tier selection + A/B compare (pure,
  tested).
- `scripts/er_matcher/config.yaml` — right-size for the real corpus.
- A thin orchestration script/entrypoint for the benchmark→scorecard→full-run flow.
- Tests alongside (`test_perf_report.py` extended, new sweep/tier/matrix tests).

## Open questions / risks

- **Measured seq_len unknown until the smoke runs** — if real citation strings push
  P95 near the 1024 cap, bf16 may not fit L4/A10G and QLoRA becomes the cost lever
  that keeps it off A100. The benchmark surfaces this.
- **Modal GPU availability/pricing** for L4/A10G/A100 tiers — rates live in a config
  table; confirm current Modal rates at run time.
- **QLoRA quality delta** on this corpus is unknown — that's precisely what the A/B
  measures; the human decides from data.
- **Eval scope**: SP2's held-out eval is basic (overall match-F1 on the test split);
  the full per-benchmark parity story is SP3.
