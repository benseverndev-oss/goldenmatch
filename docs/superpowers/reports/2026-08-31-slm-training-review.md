# How GoldenMatch trained its SLMs — a review

**Date:** 2026-08-31
**Scope:** the OSS ER-matcher small language model (Qwen2.5-1.5B-Instruct + LoRA SFT),
its data pipeline, training/eval harness, publishing path, product integration, the
GBDT student distilled off it, and the mechanistic-interpretability program built on it.
**Sources read:** `origin/main` @ `8467b5ab6` — `scripts/er_matcher/**`,
`packages/python/goldenmatch/goldenmatch/core/er_matcher/**`, `core/_llm_loader.py`,
`docs/superpowers/{specs,plans,notes}/2026-07-2*`, `docs/design/2026-08-0{2,3}-15b-*`,
`.github/workflows/publish-er-matcher.yml`, and the PR history (#2154 → #2369).

> Note: the current checkout sits on `audit/gh-issues-review`, which predates most of
> this work. Everything below was read from `origin/main`.

---

## 1. Why an SLM at all

The thesis, stated in the spec (`2026-07-26-oss-er-matcher-llm-boost-design.md`) and
sharpened in the interp handoff:

Production ER has not adopted AI for the *core match decision* for three reasons —
**cost**, **explainability/auditability**, and **privacy** (you cannot send PII to an
API). Classical Fellegi–Sunter already hits ~0.93–0.97 pair-F1 on clean structured PII,
so the AI wedge is the *hard* residual: messy text, product/cross-source, low overlap.

Inside the codebase there was a concrete gap: `llm_score_pairs` was the one capability
declared "n/a — model/network-backed, stays host" across every parity surface. It needed
an API key. A shipped OSS matcher turns that into a self-contained, offline, no-key
option — advancing the three North Star commitments (**zero-config**, **never-black-box**,
**approach-the-expert**) at once.

The task itself is narrow and short-context (two records + one question), which is
exactly where a small fine-tuned model competes with a frontier zero-shot model.

---

## 2. Chronology (what shipped, in order)

| PR | What |
|---|---|
| #2154 | Spec + plan + P1/P2/P5/P6 foundation (prompt contract, synthetic generator, eval harness, registry) |
| #2170 | P3 trainer + P3a perf gate + P4 publish workflow (GPU-work scaffolding, nothing run yet) |
| #2178 | Fix the Modal training image so it actually builds + imports |
| #2195, #2200 | Multi-source data pipeline (Phase 1a) + reproducible fetch/normalize |
| #2205 | SP2: training-run + cost-benchmark machinery (GPU tiers, learning-curve sweep, A/B config matrix) |
| #2215 | `eval_model` entrypoint — held-out match-F1 via generative inference |
| #2222 | SP3: zero-shot eval on unseen DeepMatcher benchmarks + post-hoc temperature calibration |
| #2237 | SP3.5 "honest yardstick": entity-level splits, FS-mined hard negatives, FS-driven soft targets |
| #2241 | Phase 1b (lean): rich synthetic generator — data diversity improves zero-shot (+3 pts) |
| #2260 | Local-model integration: `provider="local"` in the LLM scorer, `goldenmatch llm serve-local`, closed-loop refit |
| #2270–#2284 | Drop HuggingFace hosting for GitHub Releases; publish workflow fixes; pin the (later withdrawn) 3B GGUF |
| #2288 | **Ship 1.5B (Apache-2.0) as the default; withdraw the 3B pin** |
| #2306 | Distilled band-override scorer — spec, plan, Phase 0 harness |
| #2354 | Product-matching bench on Amazon-Google (later re-labelled as contaminated/in-distribution) |
| #2369 | Mechanistic interpretability: decision geometry, occlusion, and the causal claim that did not survive |

---

## 3. The contract that everything hangs off

`goldenmatch/core/er_matcher/prompt.py` is the single source of truth shared by
**four** consumers: training-data rendering, the trainer's SFT target, inference
(both server and in-process), and the eval harness.

- `SYSTEM_RUBRIC` — terse, deterministic task rubric. The load-bearing line is
  **"a field missing on one side is NOT a conflict"** (ER records are sparse).
- `serialize_pair_v1(a, b)` — sorted union of both records' keys, every field rendered
  for **both** sides, `None`/empty → the explicit `(missing)` sentinel. Sorted order
  means dict-insertion order can't perturb the rendering.
- `render_target(match, confidence, reason)` — **compact** JSON
  (`separators=(",", ":")`), i.e. `{"match":true,...}` with no space after the colon.
- `parse_verdict(text)` — lenient; tolerates fences/prose; **any failure returns `None`
  = ABSTAIN**, never a raise. This mirrors the pre-existing hosted-LLM fallback contract.
- `SERIALIZER_VERSION = "v1"` is part of the model card. `train.py::TrainConfig.validate`
  refuses to train if the config's version ≠ the runtime's, and
  `registry.resolve_model` refuses to *load* a model whose pin says a different version.
  A serializer change is a new model revision, by construction.

**The compact-JSON detail became load-bearing twice.** Every fast eval path teacher-forces
the prefix `{"match":` and reads the next-token logits over `true`/`false`. Because the
target is compact, those ids must be resolved from `tok.encode("true")` with **no leading
space**. Resolving `" true"` instead would silently corrupt every P(match) the harness
produced. Both `eval_model` and `zeroshot_eval` assert single-token ids and print a
mean-P(match) separation line (true vs non-match) as the backstop.

---

## 4. Training data — four generations of it

### 4.1 Gen 1: pure synthetic (`gen_pairs.py`)

Stdlib-only generator, no faker. Entities across `people`/`healthcare`/`business`;
positives are realistically corrupted copies of an entity (typos, case, whitespace,
**nicknames**, abbreviations, dropped fields, phone/email reformatting); easy negatives
are random distinct entities; hard negatives are *deceptively similar* distinct entities
(shared surname / near-twin name, conflicting strong identifiers).

Guarantees locked by tests: same seed → byte-identical output; entity-level splits with
no entity in two splits; negatives only pair entities within the same split; one
`--holdout-domain` fully reserved to `test`; ~50/50 class balance.

Seeds are generated by us, so **there is no license to violate** — the deliberate posture
for a redistributable model.

### 4.2 Gen 2: multi-source blend (`build_corpus.py` + `sources.yaml`)

Heterogeneous schemas on purpose — mixing product `{title,brand,price}`, person
`{name,address,dob}`, citation `{authors,venue,year}` improves robustness, and the shared
serializer renders arbitrary fields.

| Source | License | Mechanism | In training corpus? |
|---|---|---|---|
| FEBRL | MPL-1.1 (synthetic person) | bundle | yes |
| Leipzig `abt_buy`, `amazon_google`, `dblp_acm`, `dblp_scholar` | **CC-BY** (attribution required) | bundle | yes |
| synthetic (Phase 1b) | CC0-1.0 | generate | yes |
| Magellan/DeepMatcher | cite-only | fetch | **no — eval only** |
| NCVR (real voter PII) | public-record, no redistribution grant | fetch | **no — never bundled** |

Licensing discipline is enforced structurally, not by convention:
- `build_corpus` **constructs every configured source** (so a broken `sources.yaml` fails
  loudly) but only `bundle`/`generate`, non-`eval_only` sources contribute rows.
- `fetch` sources require `GOLDENMATCH_ALLOW_FETCH=1` and are never touched during a
  training build — no network during corpus assembly.
- `manifest.json` records every source's license + attribution *including* the eval-only
  ones that contribute nothing, for the model card.
- `--cap N` truncates oversized sources but **interleaves match/no_match round-robin
  first**, because both bundled loaders emit all positives before any negative — a naive
  `rows[:cap]` would silently produce an all-match slice.

### 4.3 Gen 3: the "honest yardstick" (SP3.5, #2237)

Three coupled changes that made the numbers *worse* on purpose:

1. **Entity-level splits via connected components over gold edges**
   (`sources/splits.py`) — kills record-level leakage.
2. **FS-mined hard negatives** (`fs_enrich.select_hard_negatives`) — take goldenmatch's
   own Fellegi–Sunter posterior, keep gold non-matches whose FS score lands in
   `[tau-delta, tau+delta]`, sort by closeness to `tau` (hardest first), cap per split.
   Gold is the truth; FS only selects *difficulty*.
3. **FS-score-driven soft confidence targets** (`fs_enrich.soft_confidence`) — replace
   the fixed 0.9/0.1 labels with a target compressed toward 0.5 near the decision
   threshold. Gold picks the direction, the score's distance from `tau` picks how extreme.
   Never 0 or 1 (saturated targets push logits to extremes and hurt calibration).

**Result: in-distribution F1 0.983 → 0.970, and the drop was the win.** Zero-shot was
neutral (Walmart 0.640→0.645, Beer 0.897→0.897), which is what told the team the next
gain had to come from data *diversity* or a different base, not more of the same.

Corpus at that point: 17,682 / 3,718 / 3,507 rows, 420 distinct FS-driven confidence
values (~0% at the old constants), 747 mined hard negatives.

### 4.4 Gen 4: rich synthetic (Phase 1b lean, #2241)

`scripts/er_matcher/synthetic/` — a pure, box-tested package: census-weighted Zipf name
sampler (vendored surnames so it stays goldenmatch-import-free), CRM/organization/business
schemas each declaring a `strong_id` (never corrupted — it's what makes a same-name pair a
true negative) and a `name_field` (what hard negatives collide on), plus five corruption
channels with light/heavy profiles.

**Hypothesis tested cheaply against the SP3.5 yardstick: does diversity improve zero-shot?
Answer: yes.** ~4k diverse pairs (+24% train data):

| Benchmark | before | after |
|---|---|---|
| Walmart-Amazon (product) | 0.645 | **0.680** |
| Beer (product) | 0.897 | **0.929** |
| iTunes-Amazon (entity) | 0.514 | 0.519 (flat) |
| Fodors-Zagats (guard) | 1.0 | 1.0 |
| in-distribution | 0.970 | 0.971 |
| in-distribution ECE | 0.0105 | **0.0049** |

Transfer landed on the highest-**headroom** benchmarks, not the ones whose *shape* matched
the synthetic data. iTunes stayed flat because its failure mode is different — it
over-predicts matches (mean P(match) 0.64 on non-matches, ECE 0.47, T=4.5) and needs harder
negatives, not more diversity.

**A benchmark-design lesson fell out of this:** Fodors-Zagats was chosen as the matched
benchmark and baselined at **F1 = 1.0 (saturated)** — it could not show improvement. It was
swapped mid-run for iTunes-Amazon (real headroom). *Check baseline headroom before trusting
a benchmark as an improvement signal.*

---

## 5. The training recipe

`scripts/er_matcher/config.yaml` (committed, so a run is reproducible from base + revision
+ LoRA shape + LR + epochs + seq-len policy + packing + seed):

```yaml
base_model: Qwen/Qwen2.5-1.5B-Instruct   # Apache-2.0, redistribution-clean
lora_r: 16 / lora_alpha: 32 / dropout 0.05
lora_target_modules: [q,k,v,o,gate,up,down]_proj    # all attention + MLP projections
learning_rate: 2e-4 / epochs: 2.0 / cosine / warmup 0.03 / weight_decay 0.0
per_device_batch: 16 x grad_accum: 2                # effective batch 32
seq_len_percentile: 95 / cap 1024 / multiple_of 64  # MEASURED, not fixed
packing: true / group_by_length: true
bf16: true / flash_attention_2: true / qlora_4bit: false
match_confidence 0.9 / nomatch_confidence 0.1       # fallback only; rows carry their own
eval_fraction: 0.1 / seed: 20260726 / dataloader_workers: 8
```

Design choices worth calling out:

- **The trainer is split pure/impure.** `train.py` holds every behaviour-bearing decision
  (config load + validation, seq-len statistic, chat-target construction, step estimate)
  with **zero heavy imports**, so it imports and unit-tests on a CPU box with no
  torch/trl installed. `_train_runtime.py` holds the GPU wiring and is imported *inside*
  `main()`. `load_config` rejects unknown keys so a typo can't silently no-op a hyperparameter.
- **`max_seq_len` is measured, not guessed.** P95 of the *chat-templated* token lengths
  (via injected `tokenizer.apply_chat_template`, so per-turn special tokens count — a
  naive content-join under-measures and makes truncation more common than intended),
  rounded up to a multiple of 64, capped at 1024. For short ER pairs this is the top
  memory/speed lever versus a 2k/4k default.
- **Sequence packing.** Which means step counts are *not* `rows / batch`:
  `estimate_total_steps` computes `sum(token_lengths) / seq_len` packed sequences. This
  is deliberately kept torch-free, because trl's packed dataloader is a lengthless
  `IterableDataset` and `len()` on it raises.
- **Gradient checkpointing with `use_reentrant=False`** — required for PEFT, and the
  memory lever that made the SFT fit (the A10G smoke OOM'd without it).
- **The SFT target teaches evidence, not prose.** `_auto_reason` emits a short
  deterministic agreement summary ("agree on email, phone" / "conflict on dob") so the
  model learns *which* fields drove the verdict without being able to learn to
  hallucinate free-form justification. The target round-trips through `parse_verdict`
  by construction, asserted in tests.
- **Row-level confidence wins over the config constant.** `example_to_messages` prefers
  the row's own FS-driven `confidence` and only falls back to 0.9/0.1 — an honest-yardstick
  label beats a fixed constant.
- **Resume is opt-in and guarded.** A non-smoke run finds `checkpoint-*` under the output
  volume and resumes; a fresh dir behaves exactly as before.

---

## 6. Nothing multi-hour launches cold — the perf gate

The hard rule from the plan: **no multi-hour GPU run is launched cold.** A cheap smoke run
(few hundred steps, small slice, cheapest adequate GPU) emits `smoke_metrics.json`; a pure,
GPU-free module turns those numbers into a GO/NO-GO.

`perf_report.py::evaluate_perf_gate` — four checks:

1. **GPU util ≥ floor** — else the data/pack path is the bottleneck, and a data-bound run
   wastes GPU dollars. Fix it before spending.
2. **Extrapolated $ ≤ budget** — linear in steps from the measured step rate.
3. **Learning curve still climbing** at the chosen data size — else more data/epochs
   won't help.
4. **Peak memory ≤ GPU capacity** (derated by a headroom factor) — confirms the tier.

Supporting pieces:
- `gpu_tiers.py` — the tier table (L4 $0.80/22–24 GB, A10G $1.10, A100-40 $2.10,
  A100-80 $2.50) and `select_cheapest_tier(peak_mem_gb, headroom=0.9)`. Note L4 has *more*
  capacity than A10G and is cheaper — real pricing, kept sorted by price.
- `sweep.py` + `run_sweep` — a learning-curve sweep over 10/25/50/100% data slices. The
  base model loads **once**; the LoRA adapter is snapshotted just-initialized and **reset
  to that snapshot before each slice**, so every fraction trains from the same start and
  the comparison isolates data volume rather than warm-starting off the previous slice.
- `config_matrix.py` + `run_benchmark.py` — the bf16-LoRA vs QLoRA-4bit A/B, producing a
  cost/quality scorecard row per config. It deliberately **picks no winner**: the
  scorecard is data for a human decision.

Instrumentation details that were got right rather than approximately right:
- `total_steps` is measured over the **full** corpus *before* the smoke slice is taken —
  measuring it over the 4k-row slice would under-count steps by the slice ratio and yield
  a falsely cheap GO.
- Wall time snapshots at `on_train_end`, excluding the trailing `evaluate()`/save, so
  `s_per_step` isn't inflated.
- Peak memory reports `max_memory_reserved` (the caching allocator's reserved pool is what
  actually presses against capacity), not `allocated`.
- `mean_util()` returns `None` (not `0.0`) when no samples were collected, so the gate
  reads "telemetry unavailable / advisory" rather than "0% util = data-bound".

---

## 7. Infrastructure — Modal

`scripts/er_matcher/modal_train.py`. App `goldenmatch-er-matcher-train`, persisted volume
`er-matcher-out`. Credentials come from a **named Modal Secret** (`er-matcher-hf`), never a
pasted token.

The image pins the whole stack — torch 2.4.0, transformers 4.44.2, peft 0.12.0, trl 0.9.6,
datasets 2.21.0, accelerate 0.33.0, bitsandbytes 0.43.3 — plus:
- a **prebuilt** flash-attn wheel matching (torch 2.4 / cu123 / cp311 / cxx11abiFALSE),
  because the sdist build runs `git submodule` (no git in `debian_slim`) and then wants the
  full CUDA toolkit;
- `rich` (trl 0.9.6 imports it without pinning it) and `pynvml` (needed for
  `torch.cuda.utilization()`, i.e. the gate's GPU-util signal);
- `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`, set **before** any `add_local_*`
  because Modal forbids build steps after local-file adds.

It mounts the shared prompt package and the trainer scripts, and mounts the training corpus
**only if the directory exists** — so the zero-shot path can run from a clean worktree with
no local training data.

GPU tiers: smoke on A10G, sweep and full run on A100-40GB (a 3B on A10G was ~7.2 s/step;
A100 is ~2.5× faster at roughly the same cost under per-second billing, so the benchmark is
both faster *and* cheaper there).

Entrypoints: `main` (smoke/full), `benchmark` (the bf16-vs-QLoRA sweep), `full`,
`evaluate` / `evaluate_detached`, `zeroshot`, `truncate_sft`.

**Two operational gotchas that cost time and are now encoded:**
- `spawn()` rather than blocking `remote()` for parallel work — both variants run in
  parallel *and* survive `--detach` after the local orchestrator disconnects; blocking
  calls run sequentially and leave the second variant unlaunched if the parent dies.
- On this Windows box, backgrounded `modal run` wrappers get reaped and `modal run --detach`
  is torn down on SIGTERM. Long jobs use `.spawn()` + `--detach` so the client exits
  cleanly. Short jobs run in the foreground.

---

## 8. Evaluation

### 8.1 The harness

`eval.py` — pure metric and gate functions (dict/number in, verdict out), unit-tested with
no model at all, mirroring `scripts/qis_gate.py`'s pattern. `run_eval` scores each split
overall **and broken down by domain**; a matcher returning `None` counts as an abstain and
is scored as a conservative no-match.

`evaluate_gate` — the ship decision, all checks must pass:
1. absolute F1 floor (default 0.80),
2. calibration (ECE ≤ 0.15),
3. **beats the hosted-LLM boost**,
4. **adds over the pure-FS baseline**,
5. no regression versus a committed baseline scorecard.

`calibration.py` — pure stdlib post-hoc temperature scaling: golden-section search on NLL,
bounded [0.05, 10], with a tie-break toward T=1.0 on a near-flat/underdetermined objective
(all-zero logits make every T identical). Plus binned ECE.

### 8.2 The fast readout

The first `eval_model` was generative — 64 tokens per pair, ~85 minutes on 3,507 pairs.
Replaced by a `fast=True` logit path: teacher-force `{"match":`, softmax over the
`true`/`false` next-token logits, one forward pass. **~6 minutes**, and it yields a
real-valued P(match) usable for temperature calibration. `fast=False` remains for exact
production-decode fidelity.

*Correction recorded later in the interp handoff:* the "10–15×" speedup is a decode-heavy /
GPU figure. On CPU it is only ~1.5× (5.3 s/pair generative vs 3.5 s readout) because
**prefill dominates**. The real CPU levers are prefix-caching the identical system rubric
(~150 of ~200 tokens per call) and GPU offload.

### 8.3 Zero-shot on unseen benchmarks

`zeroshot_eval` fetches a DeepMatcher/Magellan benchmark fresh, fits temperature `T` on its
`val` split, and reports F1 + raw/calibrated ECE on `test` — with `sota_baselines.py`
supplying published DeepMatcher/Ditto numbers as a **display-only** column, explicitly not a
gate (those baselines were trained on the benchmark's own train split; the comparison is
illustrative, not apples-to-apples).

Findings: the model is already well-calibrated raw, so temperature scaling is close to a
no-op (T sits near 1) and can nudge binned ECE up slightly — that is not a regression.

---

## 9. Base-model selection, and the licensing blocker

The spec originally recommended a **3B default**, and the first trained artifact was
Qwen2.5-3B-Instruct — published as `er-matcher-3b-v1.0.0` and pinned in #2284.

**Then the licence was checked properly.** In the Qwen2.5 lineup, 0.5B / 1.5B / 7B / 14B /
32B are Apache-2.0, but **3B and 72B are under the Qwen Research License (non-commercial)**.
The config's comment `# Apache-2.0, redistribution-clean` on the 3B was simply wrong. That
defeats the whole self-host-with-real-PII thesis: commercial users could not legally host it.

The harness is base-agnostic, so the response was a head-to-head A/B on an Apache base
(same corpus, same bf16 LoRA recipe, per-model output dirs, `--model-subdir` eval):

| | in-dist F1 | zero-shot iTunes | NCVR real-person (held-out) |
|---|---|---|---|
| 1.5B | 0.983 | 0.675 | 0.859 / R 0.753 |
| 3B (non-commercial) | 0.985 | 0.535 | 0.984 / R 0.971 |
| 7B | 0.9865 | **0.963** | 0.961 / R 0.927 |
| **1.5B + NCVR-augmented** | 0.9825 | — | **0.998 / R 0.997 / P 0.999** |

Two levers closed the 1.5B's person-recall gap, and the second is the interesting one:

1. **Threshold.** The 1.5B's precision surplus (0.999) converts to free recall — moving the
   match threshold 0.5 → 0.2 lifted NCVR recall 0.75 → 0.84 at *zero* extra false positives.
   But ~47% of the misses were confident (p < 0.1), so thresholding alone is not enough.
2. **Distribution-matched training.** Folding 9,959 disjoint-recid NCVR pairs into the
   corpus took recall 0.75 → 0.997. *That is the whole value of the labeling loop:* generate
   labels in **your** distribution, retrain the small model, and it masters your data.

**Shipped decision (#2288): Qwen2.5-1.5B-Instruct.** Redistribution-clean *and* measured
stronger zero-shot than the 3B (walmart 0.795 vs 0.721; in-dist F1 0.999, ECE 0.0009). The
3B pin was **nulled** so `resolve_model` refuses to serve it; 7B remains the optional heavier
Apache alternative (`url=None`, unpublished).

A related design note: for this product **precision is the protected axis** — a false merge
corrupts the golden record *and* poisons the m-estimate (`estimate_m_from_labels` only
consumes MATCH labels). Recall is converted into human-review volume by abstaining below a
confidence floor and routing no-match/abstain to review. The default adjudication policy is
"trust confident match, review the rest."

---

## 10. Publishing and distribution

Weights never enter the git tree (3B fp16 ≈ 6 GB, q4 ≈ 2 GB — 20× over GitHub's 100 MB cap,
and LFS free quota would be exhausted by one clone).

HuggingFace was the original plan; #2270 **scrapped it for GitHub Releases** in the project's
own org — same loader, one fewer dependency and no HF token in the runtime path.

`.github/workflows/publish-er-matcher.yml` — `workflow_dispatch` only, never automatic, run
only after the eval gate passes. It pulls the merged fp16 model (an https tarball or
`modal://<volume>/<path>` straight off the Modal volume), converts and quantizes via
llama.cpp (**prebuilt `llama-quantize` binary preferred** — compiling libllama OOM-killed
the runner with exit 143), sha256s the assets, attaches them **at draft creation** (the
immutable-release fix), and prints the exact registry pin to paste in.

The repo then holds only the pin. `registry.py::resolve_model` downloads on first use to
`~/.cache/goldenmatch/er-matcher`, **verifies the sha256** (re-verifying an already-cached
file too), and refuses on three grounds: unknown tier, **serializer drift**, or an
unpublished/`PENDING` pin — *you cannot verify what isn't pinned*.

---

## 11. How the model reaches the product

Two paths sharing one contract, so the artifact is identical across both:

- **Path A — local OpenAI-compatible server.** `python -m goldenmatch.core.er_matcher.serve_local
  --tier 1.5b --port 8080` resolves + verifies the GGUF and launches `llama_cpp.server` with
  `--chat_format chatml` (Qwen2.5). Then `GOLDENMATCH_LLM_BASE_URL=http://localhost:8080/v1`
  points the *existing* scorer at it — zero scoring-core change, since `_openai_base_url()`
  already supported an override. `LocalMatcherClient` is the per-pair primitive, with an
  injectable HTTP transport so it is fully testable without a running server; any transport
  or parse failure yields `None` = abstain.
  (Deliberately a runnable module rather than a Typer subcommand, so it stays off the
  `api_parity` `cli_commands` surface.)
- **Path B — in-process.** `core/_llm_loader.py` mirrors `_native_loader.py`: one env gate
  (`GOLDENMATCH_LOCAL_LLM` = `0` force-off / `1` require / `auto`), a discover order
  (explicit path → local cache → download from the pinned Release asset → abstain), pin +
  checksum verify, and **graceful abstain** — a missing extra or model returns `None` rather
  than raising into a pipeline run. Exposed as `provider="local"` in `llm_score_pairs`,
  which scores the existing 0.75–0.95 borderline band in-process. Optional extra:
  `goldenmatch[local-llm]`.

**Closing the loop (#2260):** `core/refit.py` turns the adjudicator's confident verdicts into
labels (confident-MATCH only, canonicalized and deduped, gated on a confidence threshold),
feeds them to the existing `estimate_m_from_labels`, and returns the refined `EMResult` as a
**suggestion** — suggest-mode only, no config mutation, no disk write, with an explicit
`persist` step. FS → borderline band → local SLM → labels → refit FS. Active learning, with
a human-visible apply step.

---

## 12. The SLM as a teacher — distilling to a µs student

The most consequential follow-on (`2026-07-31-distilled-band-override-scorer-design.md`,
harness in #2306).

**The problem.** FS resolves the bulk locally (0.93 pair-F1 at recall 1.0 on
`historical_50k`) but, being linear with a conditional-independence assumption, leaves an
uncertain band around the threshold ([0.55, 0.65]) where its per-threshold accuracy is only
~0.64–0.71. The 1.5B *is* accurate on that band — **0.885, 97% label-precision on its match
calls** — but at ~3.9 s/pair on 4-core CPU, and the band is ~67k pairs / ~42k distinct, a
live per-pair boost is **~70 hours**. Latency micro-optimizations buy 3–5×; the workload
needs ~10,000×.

**What was ruled out, by measurement:**
- **Global FS refit on band labels — neutral-to-harmful.** Even a *perfect oracle* labeler
  via iterative refit could not beat baseline (0.927 → 0.913). Labeling only the band gives
  an unrepresentative sample: hard positives bias `m` pessimistic, hard negatives bias `u`
  high (the pos+neg variant collapsed recall 0.83 → 0.50). FS's model class simply cannot
  absorb the band's nonlinear field interactions by re-estimating m/u.
- **Live per-pair LLM at query time** — perf-impossible at scale.

**The answer: we train, users consume.** The teacher (the SLM) and the distillation run once
in *our* pipeline. Users get a pinned, sha256'd student artifact — fast, accurate, fully
local, no LLM dependency at all. Same distribution model as the GGUF itself.

- **Override, not refit.** The student replaces FS's decision *on band pairs only*; non-band
  pairs keep FS. This sidesteps the unrepresentative-sample failure — it's a local
  classifier, not a global re-estimate.
- **Features are domain-agnostic by design** (`band_student/features.py`): per shared field
  `[jaro_winkler, token_sort, exact, both_present]`, plus the FS score. The student learns
  *how to combine similarity signals*, not entities — which is what could let a centrally
  trained artifact transfer to unseen user data.
- **Model:** `HistGradientBoostingClassifier` (200 iters, depth 4, lr 0.1, L2 1.0), with an
  NNUE-style quantized pair-head as the follow-on for a pure-integer score-core kernel.

**Measured on `historical_50k`:**

| | band accuracy | end-to-end dedupe F1 |
|---|---|---|
| FS baseline | 0.64–0.71 | P 0.971 / R 0.834 / **0.897** |
| the 1.5B teacher | 0.885 | — |
| student on the **teacher's** 600 labels | **0.880** (≈ matches the teacher) | P 0.971 / R 0.889 / **0.928 (+0.031)** |
| student on **gold** labels (ceiling) | 0.959 / F1 0.967 | P 0.973 / R 0.920 / **0.945 (+0.048)** |

At **2.86 µs/pair** — ~1.4 million× faster than the 1.5B. The gain is recall: FS scored true
matches in the band below its operating threshold; the student promotes them, precision held.

Load-bearing open gate before any default-on: **transfer**. +3.1/+4.8 is one dataset;
febrl3/synthetic are already solved (no band → confirmed no-op). It needs training on a
diverse corpus with held-out evaluation, schema normalization into a fixed feature space,
possibly a small family of per-domain students, and a CI transfer panel.

---

## 13. Understanding the trained model — the interpretability program (#2369)

This is the part that goes well beyond a normal fine-tune, and it is deliberately built in
two layers: **Layer 1 the math** (linear-algebra soundness only, human readability
explicitly irrelevant), then **Layer 2 the translation** — because building the abstraction
first is the "linguistic bias" trap the program rejects.

### Layer 1 — geometry, then causality

On the CPU box via the pinned GGUF (`interp/decision_geometry.py`), against **hard negatives**
(different person, same surname soundex — the blocking look-alike regime, not random
records): 5-fold linear-probe accuracy **0.991**, a single diff-of-means axis fit on train
generalizing to held-out at **0.967 AUC**, top-8 PCs recovering **0.988** of the 1536-dim
probe. Low-dimensional, ~4–8 effective dims, sharper on hard negatives than random ones —
so the confound that would have made it fake is ruled out.

On GPU (`interp/modal_interp.py`), a 28-layer sweep tells the mechanistic story: the match
direction is **already a near-perfect separator at layer 1** but as a distributed ~8D code
(top-1 PC ≈ chance); it **peaks at L13** (dir_auc 1.000); and from L15→28 it **concentrates
onto the dominant residual axis** (top-1 PC → 0.96) as the model commits.

An SAE on 848k layer-14 activations (expansion 8 → 12,288 features, L0 ≈ 212) found **no
single monosemantic feature** — the top features correlate with the label at only 0.55–0.60,
consistent with a distributed code.

Single-layer steering barely moved the verdict, because the direction is redundantly encoded
across depth. **Multi-layer** steering across L8–20 drove mean P(match) from **0.000 (c=−4)
through 0.385 (baseline) to 1.000 (c=+4)** — monotonic, full range.

### Layer 2 — translation, and the honest numbers

Regressing each pair's projection onto the proven direction against per-field jaro-winkler
agreement gave: **first_name 0.42, birth_place 0.30**, occupation 0.15, postcode 0.08,
**surname 0.04, dob 0.01** — with R² = 0.51 *against the projection*. Independently, SAE
features labelled by field converge on the same story. Surname ≈ 0 is *predicted* (the hard
negatives share surname soundex by construction, so it carries no information among them).

This was productized as `core/er_matcher/explainer.py` + `LocalLlamaAdapter.score_and_explain`
— pure, model-free (jaro-winkler + a weight table), schema-agnostic, honest about its bound.

### What the discipline caught (the valuable part)

The standing rule in this thread — **when a number looks too good, hunt the confound first** —
repeatedly overturned its own results:

1. **R² 0.51 was measuring the wrong target.** The projection is a lossy 1D shadow of an ~8D
   decision. Against the model's *actual* P(match) on a **cluster-disjoint** split, the
   shipped weights score **0.27 ± 0.07** (5 seeds). Published as 0.51, it was *optimistic*.
2. **A record-disjoint split leaks ~+0.22.** In `historical_50k` a cluster is one entity with
   several corrupted records, so a record-disjoint split puts the *same entity* on both sides.
   Use cluster-disjoint for anything published.
3. **Earlier 0.87/0.97/0.98 figures did not reproduce** and are explicitly marked
   do-not-cite. Single-seed numbers here are not publishable — seed spread exceeds most
   effects being measured.
4. **A predicted improvement was wrong and was corrected in the doc.** A logit link was
   predicted to help; it *lowered* the rows that matter and doubled their variance.
5. **`edit_norm` was a shared corruption-level proxy** (corr −0.90 with the label) — the
   "high-faithfulness" 36-signal basis was partly earning its score on how the pairs were
   mined. Corruption-matched pairs dropped it 0.64 → 0.33. **The shipped 6-field weights
   were unmoved (0.27 → 0.26)** — they were measuring real per-field evidence all along.
   Notably, the **model** uses the shortcut too: accuracy 0.88 → 0.72 once corruption is matched.
6. **Re-deriving the weights from causal ablation was tried and measurably rejected**
   (held-out R² 0.10 vs 0.27, one seed negative) and reverted. The two questions differ —
   ablation asks "does the model *need* this field?", the regression asks "does this field's
   agreement *track* the verdict?" — so **both tables ship, labelled and kept apart by a
   test**: `PERSON_FIELD_IMPORTANCE` (scoring) and `PERSON_FIELD_CAUSAL_RANKING` (necessity).
   The user-visible fix is that nothing now claims the model ignores date of birth.
7. **The "causal lock" itself was qualified.** `circuit_validation` mean-ablated the top 21
   components: it removed **84%** of the layer-14 decision variance (random-21 control: 3%),
   validating the ranking — but accuracy went 0.885 → 0.887 and 99.8% of verdicts were
   identical. So does ablating **all 183**. Steering (sufficiency) and ablation (necessity)
   are different claims; the direction is a readable correlate of the decision at layer 14,
   not a bottleneck the computation must pass through. The layers 14–27 simply re-read the
   evidence from the field-token positions and rebuild the verdict.
8. **The whole effort targeted the wrong layer.** Layer 14 was chosen because the direction
   is first *readable* there. Ablating layer 14 alone changes accuracy by 0.000; the
   load-bearing window is **15–17** (ablating layers <18 collapses accuracy to chance).
   "Where a feature becomes readable" and "where the computation is necessary" are 3–4 layers
   apart in this model.
9. **The product bench was contaminated.** `bench_product_matching.py` measured
   Amazon-Google — one of the model's own *training* sources. The docstring now carries a
   retraction and a contamination warning in place of the earlier "above DeepMatcher/Ditto"
   claim.

### Two results that directly bear on the training strategy

**Truncation: generalization lives in the late layers.** In-distribution the decision is
computed early, so most depth *looks* strippable — logit lens says 25%, a trained linear
readout says **71% (k=8, F1 0.986)**, and a truncate-and-LoRA-SFT run at k=16 holds
in-distribution F1 at 0.996 vs the 0.9996 control. But cross-domain the same k=16 collapses
walmart zero-shot **0.488 → 0.179**. So:
- fixed-domain deployment: a truncated ~16-layer model is a real, shippable smaller/faster
  scorer (−43% depth);
- a general zero-shot matcher: **keep the depth** — a smaller general model needs
  *distillation*, not truncation.

**Steering is a validated but blunt control.** As a precision/recall dial it is bang-bang —
reject-all below c=−0.5, accept-all above c=+0.5, best steer F1 0.879 versus best
**threshold** 0.943, losing 0/13 matched-recall points. A threshold operates on the model's
own calibrated confidence ranking, which is the right way to pick an operating point.
Steering earns its keep only where a threshold can't reach (no exposed probability,
binary-only deployment).

**Attribution is densely redundant at every granularity examined** — a single-field
counterfactual exists for only ~19% of decisions (reproduced across person *and* product,
the most reproducible number in the thread); you need four-field counterfactuals to cover
80%; 21 of 183 components carry 90% of the layer-14 readout variance, yet ablating all 183
changes nothing; no single layer is necessary. That is a real, measured constraint on
auditable-AI claims — and it puts per-pair counterfactuals on the **review queue**, not on
every decision.

---

## 14. The honest scorecard

| Measurement | Number | Status |
|---|---|---|
| In-distribution F1 (shipped 1.5B) | **0.999**, ECE 0.0009 | in-distribution, includes near-training sources |
| Honest in-distribution (SP3.5 config) | 0.970–0.971, ECE 0.0049 | entity-split, FS-mined negatives |
| **Walmart-Amazon zero-shot F1** | **0.795** | **the one clean held-out product number** — beats DeepMatcher 0.669, below per-dataset-tuned Ditto 0.868 |
| Beer zero-shot F1 | 0.929 | held out; beats DeepMatcher 0.727 |
| iTunes-Amazon zero-shot | 0.519 | the weak spot — over-predicts matches |
| NCVR real-person (1.5B + NCVR-augmented) | F1 0.998 / P 0.999 / R 0.997 | distribution-matched training |
| Band accuracy on `historical_50k` | 0.885, 97% label-precision on match calls | ~3.9 s/pair CPU |
| Distilled band student | 0.880 (teacher labels), +0.031 end-to-end F1 | 2.86 µs/pair |
| Amazon-Google product bench (0.886) | — | **contaminated**, retracted as a comparison |
| Explainer faithfulness vs real P(match) | 0.27 ± 0.07 (0.33 high-faithfulness mode) | cluster-disjoint, hard negatives, 5 seeds |

**Positioning, as stated in the handoff:** *competitive-local, not SOTA.* Cost and privacy
are addressed by the local + **banded** deployment (not "LLM on everything"), and
explainability by an explainer whose frozen weights are measurably about as good as a fresh
fit. The thesis is a credible, evidence-backed "this could matter" — not a proven revolution.

---

## 15. Practices worth reusing on the next model

1. **One versioned prompt/serializer contract, shared by data-gen, training, inference and
   eval** — with the version checked at both train time and load time. Training/serving skew
   is made structurally impossible rather than merely avoided.
2. **Split the trainer pure/impure.** Every decision lives in CPU-testable code; the GPU
   module only wires. The pure helpers had real unit tests before a single GPU-second was spent.
3. **Gate the expensive thing on a cheap measurement of the same thing.** A smoke run + four
   pure checks stands between the team and every multi-hour run; a second pure gate stands
   between a trained model and publication.
4. **Measure the parameter instead of defaulting it.** `max_seq_len` from a measured P95, GPU
   tier from measured peak memory, step count from packing-aware token totals.
5. **Make the yardstick honest even when it lowers your number.** 0.983 → 0.970 was the win.
6. **Check licences against the source of truth before building on them** — and re-baseline
   immediately when one fails, rather than shipping and hoping.
7. **Check a benchmark's headroom before adopting it as an improvement signal.** A saturated
   benchmark can only ever say "no change".
8. **When a number looks too good, hunt the confound first.** Every headline figure in this
   program that wasn't confound-hunted turned out to be inflated: split leakage, training
   contamination, corruption shortcuts, a wrong regression target, small-n.
9. **Two measurements that disagree may both be right about different questions.** Ship both,
   labelled, with a test that keeps them apart — don't average them into something wrong.
10. **Correct the record inside the artifact.** The design docs carry their own retractions
    ("that was wrong", "do not cite these"), so a future reader inherits the correction
    together with the claim.

---

## 16. Open threads

- **The explainer has no caller.** `score_and_explain` / `explain_for_review` are a complete,
  tested API surface, but the review queue still uses the older generic `explain_pair_nl`
  path. Until it is wired, "counterfactuals on the review queue" is a capability, not a feature.
- **Product-schema weights don't exist.** The 36-signal table is person-only; walmart has no
  derived weight table at all. `layer2_abstraction` against a product probe set is the gap.
- **Redo the attribution at layers 15–17**, where the computation is necessary, not layer 14,
  where it is merely readable.
- **The band student's transfer gate** — the load-bearing requirement before default-on.
- **The spray-labeling loop** (shard the borderline band → fan out across ~30 parallel
  CPU/GGUF runner jobs → labels → `estimate_m_from_labels` + recalibrate `tau` → the band
  shrinks) is designed but not yet a shipped reusable workflow.
- **An honest head-to-head benchmark** on held-out data is the step that would turn "appears
  to address" into "demonstrably addresses".
- **Multi-source-corpus rerun of the truncate sweep**, so the walmart absolutes match the
  shipped model rather than the 2,844-row synthetic-only corpus the sweep used.
