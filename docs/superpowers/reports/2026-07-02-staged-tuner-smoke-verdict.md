# Staged-Tuner Smoke — Verdict (SP-B2 follow-on)

**Date:** 2026-07-02
**Branch:** `feat/substrate-tuner-smoke`
**Run:** Modal `gg-bench`, `--eval tuner`, wiki corpus, 7B (`qwen2.5:7b-instruct`), `GOLDENGRAPH_LLM_SEED=42`, budget=3.

## What this validated

The SP-B2 `run_staged` + `build_and_score_real` path end-to-end on real data (`erkgbench/run_substrate_tuner.py` + a `tuner` mode in `modal_bench.py`). The whole loop ran on Modal: `for_profile` picked the initial config, `build_and_score_real` built the wiki graph under `config.apply()` and scored it with the SP-A three-axis scorecard, `evaluate_gate` ran, and the winner was promoted to a full build. **The wiring is proven** — real scorecards flow through the injected seam exactly as the box-tested harness assumed.

## Result — deterministic baseline + a reproducibility problem

`for_profile` picked **`name_ci` + `chunk_extract(6,2)`** (dense wiki → chunking on), the arc's known-best config. The gate passed on round 0 → **no escalation** (`stopped=passed`, 1 round). So the deterministic tuner confirms its rule-table pick on wiki — expected, since name_ci+chunking is already the measured best and every escalation beyond it (name_ci_type, refuted levers) is known to cost recall on non-homograph prose.

| | round-0 (slice) | full build |
|---|---|---|
| presence | **1.0000** | 0.9077 |
| relational F1 | **0.8214** | 0.6667 |
| relational R | 0.6970 | 0.5000 |
| P(B) | 1.0000 | 1.0000 |
| components | 17 | 11 |

**The honest finding: the slice and full builds are the SAME corpus + SAME seeded config, yet they diverge hard (F1 0.82 → 0.67, presence 1.0 → 0.91).** I did not implement slice-subsetting, so `dataset` is the whole wiki corpus for both the round-0 build and the full build — two sequential builds of an identical `(config, corpus, seed=42)` that should be byte-identical per the seed-determinism verdict (#1360), but aren't.

## Why this matters (and the open question)

The seed-determinism verdict established that `GOLDENGRAPH_LLM_SEED` makes a build byte-identical **across containers** (single build per container). This smoke runs **two builds in one process sequentially**, and they differ by ~0.15 F1. Candidate causes (unresolved):
- 7B/ollama seed determinism not holding across two builds in one warm process (KV-cache / model-state carryover between the first and second build);
- concurrent per-doc ingest (`GOLDENGRAPH_BUILD_WORKERS`) → thread-scheduling-dependent request interleaving that the per-request seed doesn't pin;
- a state leak in the harness (less likely — `build_and_score_real` makes a fresh `PyStore` each call).

**Consequence for the tuner:** its gate/escalation decisions are made on a single-build slice read (here 0.82) while the promoted full result is a different draw (0.67). A noisy slice read means the gate could pass/fail and the escalation could route on noise. This re-raises the seed-determinism lesson at the *harness* level: **the tuner needs a reproducible (or replicated) build before its gate decisions can be trusted.**

## Baseline for SP-C

The deterministic baseline SP-C must beat on wiki/7B: **relational F1 ≈ 0.67–0.82 (noisy), presence ≈ 0.91–1.0, at P=1.0, config = name_ci+chunk.** Given the arc has explored every lever and name_ci+chunk is the measured best, an LLM proposer is **unlikely to beat this on wiki** — the honest expectation is SP-C *confirms* the deterministic pick here. SP-C's real value is on corpora where `for_profile`'s flags bite: homograph-heavy (name_ci_type) or known-schema (schema_canon+vocab), where the config choice is non-obvious.

## ROOT CAUSE + FIX (resolved — systematic-debugging pass)

The slice-vs-full divergence was **not stochastic** — it reproduced byte-identically across two independent Modal containers (build-1 = 0.8214, build-2 = 0.6667 in *both* the workers=8 and workers=1 runs), so it is **deterministic state carryover** between the first and second in-process build. Root cause found by elimination:

- **Not a missing seed** — `llm._chat` passes `seed` on *every* request at temperature=0 (llm.py:72-77).
- **Not the resolver** — goldenmatch's controller samples with a *content-derived* seed (`hash(n_rows, column_names)`, `autoconfig_controller.py:_take_sample`), deterministic given its input.
- **Not concurrency** — a `GOLDENGRAPH_BUILD_WORKERS=1` (serial) run diverged *byte-identically* to the workers=8 run, so batched inference is not the cause.
- **→ Ollama warm-server state (KV/model cache) carryover.** The seed-determinism verdict (#1360) validated reproducibility with *one build per fresh container*; the 2-builds-in-one-warm-process path was never exercised until this smoke. Build-2's requests hit an Ollama server warmed by build-1 → different decode path → different extractions despite the seed.

**Fix (verified):** `build_and_score_real` now calls `_reset_llm_state()` before each build — unloads the Ollama model (`keep_alive=0`) so every build reloads cold, matching the reproducible single-cold-build regime. Gated `GOLDENGRAPH_TUNER_RESET_LLM` (default on), Ollama-specific, no-op off a local endpoint. **Measured effect:**

| axis | build-1 vs build-2, no reset | with reset |
|---|---|---|
| presence | 1.0000 vs 0.9077 (Δ0.09) | **0.9077 vs 0.9077 (identical)** |
| relational F1 | 0.8214 vs 0.6667 (Δ**0.15**) | 0.6844 vs 0.6667 (Δ**0.018**) |

The reset collapses the build-to-build gap **~8×**; the residual ±0.018 F1 / ±1 component is the "GPU float wobble" the seed verdict already documented (metric-level, not bit-level determinism). The tuner's gate/escalation now ride on a trustworthy signal. Since the fix lives in `build_and_score_real` (not just the runner), **SP-C inherits reproducibility automatically.** For byte-identical determinism (if ever needed), run each build in a fresh subprocess/container — the reset is the lightweight in-process approximation and is sufficient here.

## Decisions / follow-ons

1. **Ship the smoke tooling + reproducibility fix** (`run_substrate_tuner.py`, `modal_bench.py` `tuner` mode, `_reset_llm_state` in `build_and_score_real`).
2. **Reproducibility RESOLVED** (above) — the blocker before SP-C is cleared; the tuner signal is trustworthy to ±0.018 F1.
3. **SP-C framing:** an LLM proposer over the `RoundReport` diagnostic, gated to beat this baseline via the now-reproducible scorecard — most informative on a homograph/known-schema corpus (where `for_profile`'s flags bite), not wiki (where the deterministic pick is already best).

## Honest caveats

- One corpus, one seed, one 7B model. The 0.67–0.82 range is two draws, not a distribution — the reproducibility issue means even the baseline number is a range, not a point.
- The tuner "passing on round 0" is the *expected* deterministic outcome; the smoke did not exercise escalation (would need a harder gate or a corpus where the initial pick fails).
