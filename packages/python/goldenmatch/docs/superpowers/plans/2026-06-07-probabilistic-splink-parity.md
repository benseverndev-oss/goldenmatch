# Plan — Probabilistic Matching Splink Parity

**Spec:** `docs/superpowers/specs/2026-06-07-probabilistic-splink-parity-design.md`
**Date:** 2026-06-07

One PR per checklist group; each lands with tests + CHANGELOG + a measured gate.

## PR 1 — Phase 0: hygiene ✅ (2026-06-07)
- [x] Isotonic (PAV) monotonicity pass + `enforce_weight_monotonicity` in `train_em`.
      Three-state `GOLDENMATCH_FS_MONOTONIC` (`warn` default / `enforce` / `off`).
      **Measured:** `enforce` *regresses* DBLP-ACM F1 0.968 → 0.941 (the inversion
      is genuine signal there), so default is `warn` (detect + log, no change) —
      the Splink posture, and value-preserving.
- [x] `scripts/bench_fs_calibration.py`: linear vs posterior × threshold grid.
      Febrl not bundled (skipped, not faked); ran on DBLP-ACM.
- [x] `_FS_CALIBRATION_DEFAULT` stays `linear` (posterior *ties*, doesn't beat;
      flipping the headline score → probability shifts downstream cluster
      thresholds → Phase 4). Fixed the stale "flipped to posterior" comment + the
      bogus 57.6%-recall figure. Fixed the mis-tuned posterior cut 0.50 → 0.99
      (`compute_thresholds`), so the opt-in posterior path now ties linear (0.968).
- [x] **Continuous EM kept, NOT deleted** — deeper inspection shows it is a
      tested public API (`tests/test_probabilistic.py`) with a TS parity port
      (`typescript/.../core/probabilistic.ts`) + fixtures. "Wired nowhere" was
      true for the *pipeline*, false for the *library surface*. `probabilistic_fast.py`
      kept (Phase 3 input).
- [x] Gate: DBLP-ACM F1 = 0.968 (no regression, default modes); 86 FS tests pass.

## PR 2 — Phase 1a: model persistence ✅ (2026-06-07)
- [x] `EMResult.to_dict/from_dict` (versioned) + `save_json/load_json` (atomic)
      + `validate_for(mk)` (`FSModelMismatchError` on field/level mismatch).
- [x] `MatchkeyConfig.model_path` + `load_or_train_em` shared seam wired into
      all three sites (core pipeline x2, TUI engine). `dedupe_df(fs_model_path=...)`
      convenience kwarg. Cache semantics: path exists -> load + skip EM; absent
      -> train + save.
- [x] Gate: round-trip equality (unit) + saved-model run skips EM, **byte-identical
      pairs on DBLP-ACM (2310 == 2310)**. 66 FS tests pass (9 new); 132 api/config
      tests green (model_path round-trips in YAML).

## PR 3 — Phase 1b: supervised m ✅ (2026-06-07)
- [x] `estimate_m_from_labels(df, mk, labels)` — m = level frequency among
      known matches (Laplace-smoothed), u from random pairs, no EM. `iterations=0`.
- [x] Adapters: `labels_from_corrections` / `labels_from_memory_store` (memory
      `Correction` decision=approve) + `labels_from_review_items` (ReviewItem
      status=approved). Duck-typed; verified against the real classes.
- [x] Gate: 200-label seed **ties** unsupervised EM on DBLP-ACM (F1 0.968 =
      0.968, so ≥ holds). Honest note: EM is already optimal on clean DBLP-ACM,
      so the supervised edge shows on noisier data. 74 FS tests (8 new); 149 incl
      review-queue/memory green; ruff clean.

## PR 4 — Phase 2: FS waterfall explain ✅ (2026-06-07)
- [x] FS decomposition: `explain_pair_fs` + `FSWaterfall`/`FSFieldContribution`
      (level, m, u, log2(m/u) bits, prior, total, posterior) in `probabilistic.py`;
      `format_fs_waterfall` renderer in `core/explain.py`.
- [x] `lineage.py` probabilistic branch (`fs_waterfall` per pair via
      `build_lineage(em_results=...)`); `goldenmatch explain --pair` waterfall
      panel; `EngineResult.em_results` exposes the trained models.
- [x] Gate: per-comparison bits sum to total (unit test, `pytest.approx`), total
      matches the scorer's summed weight, posterior reconstructs from final bits.
      93 FS/lineage/explain tests pass (4 new waterfall); ruff clean. (Pre-existing
      TUI async failures are a missing-pytest-asyncio env issue, not this change.)

## PR 5 — Phase 3a: FS on bucket/Ray/Sail (numpy) ✅ (2026-06-07)
- [x] Probabilistic matchkeys ride `score_buckets`: `_resolve_fast_path` already
      declines them (fast_path_specs=None) → falls to `_score_one_bucket`, which
      now dispatches to `probabilistic_block_scorer` (vectorized FS) when
      `mk.type=='probabilistic'`. `score_buckets(em_result=...)`; slim projection
      keeps raw FS field columns. (Used the production vectorized scorer, not the
      orphaned `probabilistic_fast.py` scalar path — simpler + already the FS scorer.)
- [x] Pipeline FS block routes through `score_buckets` when `backend=='bucket'`
      (dedupe path, pipeline.py:1331). EM still samples within-block pairs;
      `model_path` (Phase 1a) skips EM on reuse at scale.
- [x] Gate: bucket vs polars-direct **cluster parity** at N=200/1000/3000 (unit
      test `TestFSBucketParity` + `bench_fs_and_stages.py::fs_bucket_sweep`); FS
      now reaches the same N the weighted bucket path does. 168 tests green incl.
      bucket gate / multipass / febrl3 / native parity; ruff clean.
- [ ] FOLLOW-UP: match-pipeline FS site (pipeline.py:2256, target/reference mode)
      still uses the sequential path — route through `score_buckets(target_ids=...)`
      once match-mode bucket parity is validated separately.

## PR 6 — Phase 3b: native FS kernel ✅ (2026-06-07)
- [x] `score_block_pairs_fs` (Vec) in `rust/extensions/native/src/score.rs` — FS
      arithmetic (sim→level→log2(m/u)→linear/posterior), rayon/allow_threads
      scaffold reused. Registered in lib.rs. (Vec, not arrow: integrates per-block
      via `_field_values_for_block`, reaching BOTH polars-direct + bucket paths;
      per-bucket arrow batching is a future tiny-block optimization.)
- [x] `score_probabilistic_native` + `probabilistic_block_scorer` prefer it.
- [x] Bump pyproject + Cargo lockstep (0.1.3 → 0.1.4); Cargo.lock refreshed.
      hasattr() guard degrades gracefully on a stale wheel.
- [x] Gate: **2.9x** on DBLP-ACM, **byte-exact** vs numpy on non-boundary data.
      **DEVIATION: shipped opt-in (default OFF), not default-ON** — FS's discrete
      levels amplify rapidfuzz-rs-vs-Python float diffs at exact `partial_threshold`
      values (token_sort rationals) into ~0.45 score swings (one-level flip ×
      ~40-bit weights), unlike the weighted kernel's continuous tolerance. Numpy
      stays the reproducible default; native is an accept-the-tradeoff speedup.
      191 tests green; ruff clean; crate compiles.
- [ ] FOLLOW-UP: reduce boundary sensitivity (epsilon-tolerant level cuts, or
      share the sim source) before considering default-ON; per-bucket arrow kernel
      for tiny-block FS scale.

## PR 7 — Phase 4: accuracy analysis from labels ✅ (2026-06-07)
- [x] `core/evaluate.py`: `threshold_sweep` (P/R/F1 per cut, single descending
      sweep), `recommend_threshold` (max-F1 op point), `fs_model_report`
      (per-comparison m/u/log2(m/u) bits + prior + convergence — the m/u chart
      data), `probability_two_random_records_match` (EM within-block prior λ).
- [x] `goldenmatch evaluate --threshold-sweep`: operating-point table +
      recommended cut + FS m/u model report (via MatchEngine scored_pairs +
      em_results); `--output` carries a `threshold_sweep` JSON block.
- [x] Gate: CLI emits the threshold table + recommended cut on an FS run
      (`TestThresholdSweepCLI`); DBLP-ACM recommended posterior cut = 0.9999
      (F1 0.968), confirming Phase 0. 103 evaluate/probabilistic tests green; ruff clean.

## PR 8 — Phase 3c: distributed FS validation (bench wired, UNRUN)
- [x] Bench harness wired: `scripts/bench_fs_distributed.py` (synthetic data with
      KNOWN injected dup pairs → wall + peak RSS + P/R/F1 via `backend=bucket`)
      + `.github/workflows/bench-fs-distributed.yml` (`workflow_dispatch` only,
      `large-new-64GB`, opt-in native via `fs_native`, builds the native ext).
      Smoke-validated at 4K rows (R=1.0). YAML valid, ruff clean.
- [ ] RUN IT: dispatch `bench-fs-distributed.yml` at rows=5000000 on 16c/64GB;
      record wall/RSS/F1. This is the actual gate — deliberately NOT asserted from
      code (needs a real beefy runner). Once green, Splink parity is complete.
