# Plan — Probabilistic Matching Splink Parity

**Spec:** `docs/superpowers/specs/2026-06-07-probabilistic-splink-parity-design.md`
**Date:** 2026-06-07

One PR per checklist group; each lands with tests + CHANGELOG + a measured gate.

## PR 1 — Phase 0: hygiene
- [ ] Isotonic (PAV) monotonicity pass on `EMResult.match_weights` in `train_em`; warn on adjust.
- [ ] `scripts/bench_fs_calibration.py`: linear vs posterior × threshold grid (DBLP-ACM + Febrl).
- [ ] Set `_FS_CALIBRATION_DEFAULT` from the sweep; fix the line-53 comment; set a measured posterior cut if posterior wins.
- [ ] Delete continuous EM (`train_em_continuous`, `score_probabilistic_continuous`, `ContinuousEMResult`) unless a caller lands; keep `probabilistic_fast.py` (Phase 3 input).
- [ ] Gate: DBLP-ACM F1 ≥ 0.968; `title` weights monotone.

## PR 2 — Phase 1a: model persistence
- [ ] `EMResult.to_dict/from_dict/save_json/load_json`.
- [ ] `MatchkeyConfig.model_path` + `dedupe_df(fs_model=...)`; skip `train_em` when supplied.
- [ ] Gate: round-trip equality; saved-model run skips EM, byte-identical pairs.

## PR 3 — Phase 1b: supervised m
- [ ] `estimate_m_from_labels(df, mk, labels)`.
- [ ] Adapter: labels from review-queue/memory corrections store.
- [ ] Gate: 200-label seed ≥ unsupervised EM F1 on DBLP-ACM.

## PR 4 — Phase 2: FS waterfall explain
- [ ] FS decomposition in `core/explain.py` (level, m, u, bits, prior, total, posterior).
- [ ] `lineage.py` probabilistic branch; `goldenmatch explain` waterfall.
- [ ] Gate: per-comparison bits sum to total; unit test.

## PR 5 — Phase 3a: FS on bucket/Ray/Sail (numpy)
- [ ] Accept `mk.type == "probabilistic"` in `score_buckets._resolve_fast_path` via `_resolve_probabilistic_fast_path`.
- [ ] Per-block compute = `score_probabilistic_vectorized`.
- [ ] Gate: bucket vs polars-direct cluster parity (extend `bench_fs_and_stages.py`); FS reaches weighted-path N.

## PR 6 — Phase 3b: native FS kernel
- [ ] `score_block_pairs_fs_arrow` in `rust/extensions/native/src/score.rs` (FS arithmetic + posterior/linear).
- [ ] Wire native dispatch in `score_buckets`; numpy pure-fallback.
- [ ] Bump `pyproject.toml` + `Cargo.toml` lockstep; republish wheel; verify symbol in published `.so`.
- [ ] Gate: native vs numpy parity; native 5× on wedge runner.

## PR 7 — Phase 3c + Phase 4: distributed validation + accuracy analysis
- [ ] 5M FS dedupe on 16c/64GB within scale-envelope budget; F1 within tolerance on labeled slice.
- [ ] Extend `evaluate`: ROC/PR + threshold→(P,R,F1) table + recommended cut; `probability_two_random_records_match`; m/u charts.
- [ ] Gate: `goldenmatch evaluate` emits threshold table + recommended cut on an FS run.
