# Design Spec: OSS fine-tuned ER-matcher for the local LLM boost

- **Date:** 2026-07-26
- **Status:** DRAFT (design only — the training run happens on GPU hardware outside the dev sandbox)
- **Author:** Claude (with benzsevern)
- **Related:** `packages/python/goldenmatch/goldenmatch/core/llm_scorer.py`, `packages/python/infermap/infermap/scorers/llm.py`

## 1. Summary

Ship a small, fine-tuned, open-source LLM that labels record pairs ("are these
two records the same entity? → match / no-match + confidence"), distributed as a
pinned model artifact (Hugging Face Hub, quantized GGUF), loaded by a thin
in-repo adapter behind the **existing** LLM-scorer seam. This turns the one
remaining black-box, paid-API-dependent capability (`llm` scorer / LLM boost)
into a self-contained, offline, no-API-key, reproducible option.

**This is on-mission.** Across the parity work, the `llm` scorer is the single
capability everywhere declared "n/a — model/network-backed, stays host." It is
the one thing a user cannot run without an external paid dependency. A shipped
OSS matcher directly advances the North Star commitments **zero-config** (works
offline out of the box), **never-black-box** (weights + training recipe are
inspectable and pinned), and **approach-the-expert** (a task-specialized model
that rivals a general large model on ER pairs).

## 2. Goals / non-goals

**Goals**
- A local, offline `llm`-boost backend that needs no API key and no network.
- Default runs on a laptop **CPU** (quantized, via llama.cpp) — no GPU required
  to *use* it.
- Accuracy that **beats the current hosted-LLM boost** (and ideally the
  zero-config FS baseline) on the repo's ER benchmarks — measured, gated, not
  assumed.
- Reproducible: pinned base model + revision, committed training config + data
  manifest, a one-command training script (run on GPU hardware).

**Non-goals**
- Not replacing the hosted-API path — it stays as an opt-in alternative (users
  with a key + a preference for a frontier model keep it).
- Not committing model weights to the git tree (see §5).
- Not training in this dev sandbox (no GPU) — this spec defines *what* and
  *how*; the training run is an out-of-band GPU job.
- Not a general chat model — a single narrow task (pairwise ER adjudication).

## 3. Background: the current seam (why integration is cheap)

`goldenmatch/core/llm_scorer.py::llm_score_pairs(...)`:
- Takes `provider` ("openai" | "anthropic") + `api_key` + `model`, auto-detected
  from env; falls back cleanly (returns unscored) when no key is present.
- **Crucially, `_openai_base_url()` (≈ line 395) already lets the "openai"
  provider point at any OpenAI-compatible endpoint** via an env override.

infermap's `LLMScorer` takes an injectable `LLMAdapter` (`scorers/llm.py`), and
is a sync-abstain-by-default scorer whose async path the engine drives.

So there are **two low-friction wiring options** (see §6) and neither requires
rewriting the scoring core.

## 4. Model choice & sizing

ER pair adjudication is narrow and short-context (two records + a question),
which is exactly where small fine-tuned models are strong (cf. Ditto and the
fine-tuned-matcher literature: a LoRA-tuned 3–7B routinely matches or beats
zero-shot frontier models on domain ER).

| Tier | Base candidates | 4-bit GGUF | Runs on | Role |
|---|---|---|---|---|
| **Default** | Qwen2.5-3B-Instruct, Llama-3.2-3B-Instruct | ~2 GB | laptop CPU | zero-config local boost |
| **Optional** | Qwen2.5-7B-Instruct, Mistral-7B | ~4.5 GB | 16 GB RAM / small GPU | heavier accuracy |
| **Rejected default** | 32B | ~18 GB | 24 GB+ / GPU | too heavy for "just runs" |

- **Recommendation: default 3B, optional 7B.** 32B is the wrong *default* — at
  4-bit it needs ~18 GB RAM and is slow on CPU; keep it out of scope unless a
  measured accuracy gap justifies a GPU-only tier later.
- **License must be redistribution-friendly** (Apache-2.0 / permissive). Qwen2.5
  (Apache-2.0) and Llama-3.2 (Llama license — check ToS for redistribution of
  a fine-tune) are the leading candidates; prefer Apache-2.0 to avoid license
  friction on the shipped artifact.

## 5. Distribution — NOT in the git tree

Model weights do **not** belong in the repo:

| Model | fp16 | 4-bit GGUF | vs GitHub 100 MB file cap |
|---|---|---|---|
| 3B | ~6 GB | ~2 GB | 20× over |
| 7B | ~14 GB | ~4.5 GB | 45× over |

GitHub hard-blocks files > 100 MB; Git LFS free quota (1 GB storage + 1 GB/mo
bandwidth) would be exhausted by a single clone, and committed weights bloat
every clone forever.

**Distribution design:**
- **Primary: Hugging Face Hub** — `benseverndev-oss/goldenmatch-er-matcher-3b`
  (+ `-7b`), publishing GGUF (q4_k_m + q8_0) + the LoRA adapter + a model card.
  Purpose-built: free hosting, revisioning, `huggingface_hub` download + cache.
- **The repo holds only the small stuff:** the loader, the prompt template, the
  pinned `(repo_id, revision, filename, sha256)`, and the eval harness. First use
  downloads to the HF cache (`~/.cache/huggingface`), like spaCy/transformers.
- **Fallback: a GitHub Release asset** (2 GB/file) if HF is undesirable — same
  loader, different URL. GH Releases still have bandwidth limits, so HF is
  preferred for a public model.
- **Pin + verify:** the loader checks the committed `sha256` after download
  (never-black-box: the exact bytes are pinned and verifiable).

## 6. Integration architecture

Two complementary wiring paths; ship **A first** (near-zero code), then **B** for
the no-server, in-process experience.

**Path A — OpenAI-compatible local server (ships first, minimal code).**
Serve the GGUF via `llama-cpp-python`'s OpenAI-compatible server, Ollama, or
vLLM; point the existing scorer at it by setting `provider="openai"` +
`GOLDENMATCH_OPENAI_BASE_URL=http://localhost:...` + a dummy key. The
`_openai_base_url()` override already supports this — the only repo work is
docs + a `goldenmatch llm serve-local` convenience command that launches the
server on the pinned model.

**Path B — in-process local adapter (no server).**
A new optional extra `goldenmatch[local-llm]` pulling `llama-cpp-python` +
`huggingface_hub`. A `LocalLlamaAdapter` implements the same
prompt→match/no-match/confidence contract, loading the pinned GGUF in-process
(CPU by default, `n_gpu_layers` if a GPU is present). Selected via
`provider="local"` in the LLM-boost config. infermap's `LLMScorer` takes the
same adapter directly.

Both paths use the **same prompt/IO contract (§8)**, so the model artifact is
identical across them.

## 7. Training data

The real work. The repo already carries labeled ER data:
- `benchmarks/` + the QIS harness (labeled, prefix-stable synthetic + real).
- Febrl3, NCVR-derived synthetic, Valentine (infermap), DQBench.

**Plan:**
1. **Assemble a pair-labeling dataset** from the above: `(record_a, record_b,
   label∈{match,no-match}, domain)`, balanced, with **hard negatives** (blocked
   pairs that are close but distinct — the cases that actually matter). Mine hard
   negatives from the FS pipeline's near-threshold pairs.
2. **Licensing checklist (blocking gate before publishing a trained model):**
   audit each source's license for (a) training use and (b) redistribution of a
   derived model. NCVR/Valentine/DQBench terms must each be cleared; drop any
   source that can't be redistributed and note it in the data manifest. Prefer
   synthetic + permissive sources for the shippable model; use restricted
   sources only for internal eval.
3. **Format:** instruction-tuned chat pairs (system = task rubric, user = the two
   records rendered by a stable serializer, assistant = the JSON verdict).
4. **Held-out split** by *entity* (not by row) to prevent leakage, plus a
   fully-held-out domain to measure generalization.
5. Commit a **data manifest** (sources, counts, license status, serializer
   version, split seed) — not the raw data unless license-clear + small.

## 8. Prompt / IO contract

Stable, versioned, shared by all paths and the training data:
- **System:** the ER rubric (what "same entity" means; how to weigh
  agreements/conflicts; that missing≠conflict).
- **User:** the two records via a **pinned serializer** (`serialize_pair_v1` —
  field: value lines, deterministic field order). The serializer version is part
  of the model card; a serializer change = a new model revision.
- **Assistant (enforced):** compact JSON `{"match": bool, "confidence": 0.0–1.0,
  "reason": "..."}`. Parsed leniently; a parse failure = abstain (the boost
  contributes nothing, never a crash), mirroring the current fallback contract.

## 9. Training recipe

- **Method:** LoRA (3B) / QLoRA (7B) — adapter-only, cheap, reproducible.
- **Hardware:** one 24 GB GPU for 3B/7B LoRA (~hours); QLoRA fits 7B on 16–24 GB.
  **Not the dev sandbox** — an out-of-band GPU job (documented runbook).
- **Artifacts:** committed `train_er_matcher.py` + `config.yaml`
  (base model + revision, LoRA rank/alpha, LR, epochs, seed) so a run is
  reproducible; output = LoRA adapter → merged → quantized GGUF (llama.cpp
  `convert` + `quantize`).
- **Determinism:** fixed seed; the config + data manifest + base revision fully
  specify the run.

## 10. Eval gates (ship only if it wins)

The model ships **only if** it beats the incumbents on the repo's benches:
1. **vs hosted-LLM boost:** on the ER-KG-Bench / QIS labeled sets, the local
   model's pair-F1 ≥ the hosted `llm` boost's (the current
   `benchmarks/.../scorecard_llm.py` path is the baseline harness).
2. **vs FS zero-config:** the LLM boost must *add* over the pure FS baseline on
   the datasets where the boost is meant to help (else it's not worth the weight).
3. **Latency floor:** median CPU latency per pair recorded; a "too slow to be
   default" result demotes 3B→a smaller tier or keeps it opt-in.
4. **Calibration:** confidence must be usable as a threshold (reliability curve),
   since the boost feeds the weighted combination.
Gate is a committed eval script producing a scorecard, run on GPU/CI-adjacent
hardware; the scorecard is committed alongside the model card.

## 11. Rollout phases

- **P0 (this spec).** Design + base-model + license decision.
- **P1.** Data assembly + manifest + licensing sign-off (blocking).
- **P2.** Train 3B LoRA → GGUF; eval vs baselines (§10). Iterate until it wins.
- **P3.** Publish to HF (pinned + sha256) + model card + eval scorecard.
- **P4.** Ship Path A (docs + `serve-local` command) — zero core risk.
- **P5.** Ship Path B (`goldenmatch[local-llm]` in-process adapter) + skip-guarded
  test (downloads a tiny fixture model in CI, or mocks the adapter).
- **P6 (optional).** 7B tier; infermap `LLMScorer` local adapter; TS parity
  (an OpenAI-compatible local endpoint works from TS today via the same server).

## 12. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Data license blocks redistribution | Licensing gate in P1; ship on synthetic + permissive sources only; restricted data stays eval-only |
| Local model loses to hosted on hard domains | Eval gate (§10) — it doesn't ship as default unless it wins; hosted path stays available |
| CPU too slow for large runs | It's a *boost* on near-threshold pairs (a small fraction), not every pair; batch; 3B-q4 on CPU is viable for that volume; document the throughput |
| Weight bloat / bad distribution | HF Hub + pinned sha256; never the git tree (§5) |
| Base-model license drift | Pin base model + revision; prefer Apache-2.0 (Qwen2.5) |
| Determinism/repro | Committed config + data manifest + seed + base revision |

## 13. Open questions

1. Base model: **Qwen2.5-3B (Apache-2.0)** vs Llama-3.2-3B (license check) — lean
   Qwen for redistribution cleanliness. Confirm.
2. Publish under `benseverndev-oss` HF org? (need the HF org + a token secret for
   the publish workflow.)
3. Is Path A (local OpenAI-compatible server) enough for v1, deferring the
   in-process adapter (Path B) to P5? (Recommended: yes.)
4. Which labeled sources clear redistribution? (P1 licensing audit output.)
