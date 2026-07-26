# Implementation Plan: OSS fine-tuned ER-matcher (local LLM boost)

- **Date:** 2026-07-26
- **Status:** P1/P2/P5/P6 merged; **P3 (trainer + P3a perf gate + Modal app + config) + P4
  (publish workflow + runbook) built** (this PR) — all `[here]` work done. Remaining is
  `[gpu]`-only: run the Modal smoke → gate → full train, then the publish workflow +
  fill the registry pins. Runbook: `docs/superpowers/notes/2026-07-26-er-matcher-gpu-runbook.md`.
- **Spec:** `docs/superpowers/specs/2026-07-26-oss-er-matcher-llm-boost-design.md`

## Decisions locked (from spec review)

- **Base model:** Qwen2.5-3B-Instruct (Apache-2.0 — redistribution-clean). 7B optional later.
- **Training data:** **fully synthetic** (generated + realistically corrupted seeds we own)
  + hard negatives mined from the FS pipeline's near-threshold pairs.
  **Restricted real datasets (NCVR/Valentine/DQBench) are EVAL-ONLY** — never in the
  shipped training manifest, never redistributed. No license gate on the shipped model.
- **v1 integration:** Path A (local OpenAI-compatible server via the existing
  `core/llm_scorer.py` `base_url` override). Path B (in-process adapter) is later.
- **GPU:** Modal (serverless GPU) via a **Modal secret** — NOT a pasted token.
- **Distribution:** Hugging Face Hub, pinned GGUF + sha256. Repo holds only loader + prompt.

Legend: **[here]** = buildable in the dev sandbox now · **[gpu]** = out-of-band Modal run.

---

## Phase 1 — Synthetic data pipeline **[here]**

Reuse the repo's generators/corruption (`packages/python/goldenmatch/tests/generate_synthetic.py`,
the QIS harness `scripts/qis_gate.py` + `tests/test_qis_harness.py`, `tests/fixtures/ncvr_synth_dupes.py`).

- **`scripts/er_matcher/gen_pairs.py`** — emit labeled pairs JSONL
  `{a: {...fields}, b: {...fields}, label: "match"|"no_match", domain, source}`:
  1. Generate synthetic entities (people/orgs/products) from faker-style generators +
     the existing domain packs — **seeds we own** (no license).
  2. **Positives:** apply realistic corruption to a synthetic original (typos,
     transpositions, nicknames, abbreviations, formatting drift, dropped fields) —
     the existing corruption harness, pointed at *our* seeds.
  3. **Easy negatives:** random distinct entities.
  4. **Hard negatives [depends on P1b]:** near-threshold pairs (see below).
  5. **Entity-level split** (train/val/test by entity id, not row) + a held-out
     *domain* for generalization. Fixed seed.
- **`scripts/er_matcher/mine_hard_negatives.py` (P1b)** — run the FS pipeline over the
  synthetic corpus, capture blocked pairs scoring just below the match threshold
  (close-but-distinct) → the negatives that actually teach the boundary.
- **`data/er_matcher/manifest.json`** — sources, counts, per-split entity ids,
  serializer version, seed, corruption profile. (Raw JSONL is gitignored / regenerated;
  the manifest + generator are the reproducible source of truth.)
- **Tests [here]:** `scripts/er_matcher/test_gen_pairs.py` — determinism (same seed →
  same pairs), no entity leakage across splits, class balance, hard-negative share.

## Phase 2 — Prompt / serializer / IO contract **[here]**

Shared by data-gen, training, and inference (one source of truth).

- **`packages/python/goldenmatch/goldenmatch/core/er_matcher/prompt.py`:**
  - `serialize_pair_v1(a, b) -> str` — deterministic field-order `field: value` rendering.
  - `SYSTEM_RUBRIC` — the "same entity" rubric (weigh agreements/conflicts; missing≠conflict).
  - `parse_verdict(text) -> {match: bool, confidence: float, reason: str} | None` — lenient
    JSON parse; **None on failure → abstain** (never crash), mirroring the current
    fallback contract.
- **Tests [here]:** serializer stability (golden), parser robustness (truncated/garbage
  → abstain), rubric versioning (a serializer change = a new model revision).

## Phase 3 — Training script (Modal-ready), PERF-GATED **[here to write, [gpu] to run]**

**Hard rule: no multi-hour run is launched cold. It is gated behind a measured smoke run
(P3a). Build the trainer optimized + instrumented from the start** (repo lesson: measure
wall-clock on the real workload before scaling).

- **`scripts/er_matcher/train.py`** — LoRA fine-tune Qwen2.5-3B via `transformers`+`peft`+`trl`
  (SFT on the P2 chat format). Reads `config.yaml`. Output: LoRA adapter → merged fp16.
  **Optimizations baked in (short-sequence ER SFT):**
  - **Sequence packing** (pack short pairs; no per-example pad-to-max) — the top lever.
  - **`max_seq_len` = measured P95** of serialized pairs (~256–384), NOT a 2k/4k default.
  - **bf16 + FlashAttention-2**; QLoRA-4bit only if memory-bound (bf16-LoRA preferred — 4-bit
    adds compute).
  - **Pre-tokenize + cache once**; length-grouped batching; enough dataloader workers →
    GPU-bound (>90% util), not data-bound.
  - Max per-step batch + grad-accumulation to the effective batch; eval-based early stop;
    adapter checkpointing (Modal preemption-safe).
  - **Instrumentation:** log tokens/s, samples/s, GPU util, step time, peak mem.
- **`scripts/er_matcher/config.yaml`** — reproducible run config (base + pinned revision, LoRA
  rank/alpha, LR, epochs, seq_len, packing, seed). Committed.
- **`scripts/er_matcher/modal_train.py`** — Modal app wrapping `train.py`; `modal.Secret` for
  HF/Modal creds (rotated — spec §1); mounts the data manifest.

### Phase 3a — Perf calibration + smoke run (GO/NO-GO before the full run) **[gpu, cheap]**
- **Smoke run:** few-hundred steps on a small data slice on the **cheapest adequate GPU
  (A10G first)**. Emits: measured tokens/s + GPU util, **extrapolated full-run wall-clock + $**,
  peak mem (→ confirm GPU tier), and a **mini learning curve** (10/25/50% data slices) to
  right-size data volume + epochs so the full run trains no more than helps.
- **Gate:** GPU util >~90% (else fix the data/pack path before spending on the full run),
  extrapolated cost within budget, learning curve still climbing at the chosen data size.
  Only on PASS do we launch the full run (P3b).
- **`scripts/er_matcher/perf_report.py`** — turns the smoke logs into the go/no-go scorecard.

### Phase 3b — Full training run **[gpu]**
- Launch only after P3a passes. Right-sized (GPU tier, batch, seq_len, data volume, epochs)
  from the smoke measurements. Checkpointed; determinism via fixed seed + pinned base + config.

## Phase 4 — Quantize + publish **[gpu]/[here]**

- **[gpu]** llama.cpp `convert_hf_to_gguf.py` + `quantize` → `q4_k_m` (default) + `q8_0`.
- **[here]** `.github/workflows/publish-er-matcher.yml` (optional) — or a documented manual
  push — to HF `benseverndev-oss/goldenmatch-er-matcher-3b`: GGUF + adapter + **model card**
  (base, license, training-data manifest summary, serializer version, eval scorecard) +
  the committed **sha256**.
- Repo pins `(repo_id, revision, filename, sha256)` in `core/er_matcher/registry.py`.

## Phase 5 — Eval harness + gate **[here]**

- **`scripts/er_matcher/eval.py`** — run the matcher (via the Path-A local server) over:
  (a) the synthetic held-out split, (b) the held-out domain, (c) the **restricted eval-only**
  sets (NCVR/Valentine/DQBench — measured, not shipped). Compute **pair-F1**, a **reliability
  curve** (confidence calibration), and **median CPU latency/pair**. Reuse
  `goldenmatch/core/evaluate.py::score_quality`.
- **Gate (ship only if it wins):**
  1. pair-F1 ≥ the hosted-LLM boost baseline (`benchmarks/.../scorecard_llm.py`).
  2. adds over the FS zero-config baseline where the boost is meant to help.
  3. calibration usable as a threshold; latency within the "default" budget (else demote tier).
- **`scripts/er_matcher/baselines/er_matcher_scorecard.json`** — committed scorecard (bless in CI).

## Phase 6 — Path A integration **[here]**

- **`goldenmatch llm serve-local`** CLI command — launches `llama-cpp-python[server]` (or Ollama)
  on the pinned GGUF (auto-downloaded via `huggingface_hub`, sha256-verified), exposing the
  OpenAI-compatible endpoint.
- Wire the boost: `provider="local"` in the LLM-boost config → sets the OpenAI base_url to the
  local server (the `_openai_base_url()` override already supports this — minimal core change:
  add the `"local"` provider alias + auto-point at the served port).
- **Docs:** `packages/python/goldenmatch/docs/...` "offline LLM boost, no API key" quickstart.
- **Test [here]:** skip-guarded — mock the local endpoint (or a tiny fixture GGUF in CI) →
  assert the boost contract (verdict parse → weighted-combination input).

## Phase 7 — Path B in-process adapter (LATER)

- `goldenmatch[local-llm]` extra (`llama-cpp-python` + `huggingface_hub`); `LocalLlamaAdapter`
  implementing the shared contract in-process (CPU default, `n_gpu_layers` if GPU). Wire
  infermap's `LLMScorer(adapter=...)`. TS parity via the same local OpenAI-compatible endpoint.

---

## What I need from you

- **HF org + token:** create/confirm a `benseverndev-oss` HF org and add an `HF_TOKEN` repo
  secret (for publish) — and a **rotated** Modal token as a Modal secret (for training).
- **Trigger the GPU run:** P3's Modal job is the one step I can't run here — you kick it (or
  approve me to, once the Modal secret is set and the data + script are built + reviewed).

## What I'll build here, in order

P1 (data gen + hard-negative miner + manifest + tests) → P2 (prompt/serializer/parser + tests) →
P3 (train.py + modal_train.py + config, unrun) → P5 (eval harness + gate) → P6 (Path A CLI +
integration + skip-guarded test) → P4 publish workflow. Then you run P3 on Modal, we check the
P5 gate, and if it wins, publish (P4) and flip Path A on.

## Testing / CI

- Each `[here]` phase ships with pytest coverage (determinism, no-leakage, parser-robustness,
  contract). An `er_matcher` CI lane runs the box-safe tests (data-gen + prompt + eval-logic +
  Path-A contract with a mocked endpoint). The GPU train + real-model eval run out-of-band; the
  blessed scorecard is committed and drift-checked.

## Risks (from spec, plus plan-level)

- **Synthetic realism gap:** synthetic-trained model underperforms on messy real data →
  mitigated by the restricted real-data *eval* (§5) catching it before ship; iterate corruption
  realism if the eval gap is large.
- **Latency as default:** if 3B-q4 CPU is too slow for the boost volume, keep it opt-in / add a
  smaller tier — the boost only fires on near-threshold pairs, not every pair.
- **Scope creep into Path B/7B:** explicitly deferred; v1 is Path A + 3B only.
