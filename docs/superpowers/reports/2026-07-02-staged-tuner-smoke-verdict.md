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

## Decisions / follow-ons

1. **Ship the smoke tooling** (`run_substrate_tuner.py` + `modal_bench.py` `tuner` mode) — it's the reusable harness for the baseline and for SP-C's A/B.
2. **BEFORE SP-C: fix or quantify the build reproducibility.** Either (a) make the double-build reproducible (investigate the seed/concurrency interaction; possibly `GOLDENGRAPH_BUILD_WORKERS=1` for tuning runs), or (b) replicate each config's build N times and gate on the median — else the tuner (and SP-C's self-verify) chase noise. This is the priority follow-on, not SP-C itself.
3. **SP-C framing:** an LLM proposer over the `RoundReport` diagnostic, gated to beat this baseline via the (reproducible) scorecard — most informative on a homograph/known-schema corpus, not wiki.

## Honest caveats

- One corpus, one seed, one 7B model. The 0.67–0.82 range is two draws, not a distribution — the reproducibility issue means even the baseline number is a range, not a point.
- The tuner "passing on round 0" is the *expected* deterministic outcome; the smoke did not exercise escalation (would need a harder gate or a corpus where the initial pick fails).
