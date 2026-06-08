# Probabilistic Matching — Splink Parity

**Date:** 2026-06-07
**Status:** Draft
**Scope:** Bring the Fellegi-Sunter (`type="probabilistic"`) matchkey to parity with Splink across model lifecycle, supervised training, FS-native explainability, calibration, and — leveraging the existing bucket/native/Ray/DataFusion substrate — scale-out.

---

## Problem

An audit of `core/probabilistic.py` (2026-06-07) found the FS *algorithm* is faithful to Splink and accuracy-competitive — reproduced **P=0.978 / R=0.958 / F1=0.968** on the bundled DBLP-ACM dataset — but the surrounding *engine* is materially behind Splink:

1. **No scale-out.** FS runs only on the single-node polars-direct path (`pipeline.py:1331`). Every scale backend declines it: `score_buckets.py:284` (`mk.type != "weighted"`), `datafusion_backend.py:11` (`NotImplementedError`), and `distributed/scoring.py:85` forces `backend="bucket"` per partition — which declines FS. All 25M–100M machinery is built around the *weighted* matchkey.
2. **No model lifecycle.** `EMResult` is a bare dataclass with no save/load; every run retrains EM from scratch. Splink is train-once → `save_model_to_json` → reuse.
3. **No supervised training.** `train_em` is unsupervised only; there is no `estimate_m_from_labels` analog, despite a review-queue/memory/LLM corrections store that could feed it.
4. **No FS-native explainability.** `explainer.py` reports `score × weight` (the *weighted* decomposition), not the FS match-weight waterfall (per-comparison log₂(m/u) bits) that is Splink's signature.
5. **Calibration gap.** Default scoring is `linear` min-max of summed weights — admittedly *not* a probability (`probabilistic.py:40`). The `posterior` mode that *is* calibrated measured worse F1 (P=0.806/R=0.997/F1=0.892) at its default 0.5 cut, and the "default flipped to posterior" comment (`probabilistic.py:53`) was never honored.
6. **Dead/experimental code.** `probabilistic_fast.py` (221 lines, parity-tested) and the continuous/Gaussian EM (`train_em_continuous`, `score_probabilistic_continuous`) are wired into nothing in production.
7. **Weight monotonicity smell.** Trained DBLP-ACM `title` weights were `[-2.52, 28.58, 11.86]` — the *partial* level outweighs *exact* agreement. EM estimates m/u per level with no monotonicity constraint.

**Key sizing correction:** the scale-out substrate (bucketing, rayon, Arrow zero-copy, exclude handling, per-partition Ray wiring, DataFusion/Sail spine) is scorer-agnostic *orchestration*. The only weighted-specific coupling is the native kernel's ~30-line inner loop (`score.rs:115`, `score_sum += score·weight; combined = score_sum/total_weight`). `probabilistic_fast.py` already resolves the FS per-field spec in the shape the bucket fast-path consumes. So scale-out is integration + one new kernel function, not a greenfield engine.

---

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Scale-out strategy | Reuse bucket/Ray/Sail orchestration; add FS compute, not new plumbing | Substrate is scorer-agnostic; distributed FS falls out of bucket FS |
| First FS-at-scale compute | Existing `score_probabilistic_vectorized` (numpy) | Unlocks bucket+Ray+Sail scale-out with zero Rust; native 5× is a follow-up |
| Native FS kernel | Separate `score_block_pairs_fs_arrow`, not a generalized combiner | Keeps the proven weighted kernel untouched; FS arithmetic is distinct enough |
| Model persistence format | JSON (`EMResult.to_dict/from_dict` + `save_json/load_json`) | Mirrors Splink `save_model_to_json`; human-diffable; survives Ray worker serialization |
| Supervised m | `estimate_m_from_labels(df, mk, labels)`, labels from review/memory store | Biggest accuracy lever; reuses existing corrections infrastructure |
| Calibration default | Decide by measured sweep; fix threshold if `posterior` | Don't ship a default that loses F1; comment must match code |
| Monotonicity | Pool-adjacent-violators isotonic pass on match_weights, warn on fire | Standard FS hygiene; Splink surfaces this |
| Dead code | Wire `probabilistic_fast` (Phase 3) or delete; delete continuous EM unless a caller lands | No tested-but-uncalled production modules |
| SQL-templated FS (Splink-on-DuckDB) | Out of scope | Redundant once bucket+native+Ray scales; revisit only on a measured 50M+ FS ask |

---

## Phase 0 — Correctness & hygiene

- **Monotonicity guard** in `train_em`: isotonic (PAV) pass over each field's `match_weights` so levels are non-decreasing; `logger.warning` when it adjusts. Gate: DBLP-ACM `title` becomes monotone, F1 ≥ 0.968 (no regression).
- **Calibration decision**: commit a `scripts/bench_fs_calibration.py` sweep (linear vs posterior × threshold grid on DBLP-ACM + Febrl). Pick the default by max F1; align `_FS_CALIBRATION_DEFAULT` and the line-53 comment. If `posterior`, set a measured default cut (not 0.5).
- **Dead-code triage**: keep `probabilistic_fast.py` (Phase-3 input). **Resolved: keep continuous EM** — it is a tested public API with a TS parity port, not dead code (the audit's "wired nowhere" held for the pipeline, not the library surface).

## Phase 1 — Model lifecycle (highest operational leverage)

- **1a. Persistence**: `EMResult.to_dict()/from_dict()`, `save_json(path)/load_json(path)`. Add `MatchkeyConfig.model_path` (and a `dedupe_df(..., fs_model=...)` kwarg). When a model is supplied, skip `train_em`. Gate: round-trip equality; second run with saved model skips EM and is byte-identical.
- **1b. Supervised m**: `estimate_m_from_labels(df, mk, labels)` computes m directly from known-match pairs (Splink `estimate_m_from_label_column`). Adapter pulls labels from the review-queue/memory corrections store. Gate: seeding m from 200 DBLP-ACM true pairs ≥ unsupervised EM F1 (measured).

## Phase 2 — FS-native explainability

- FS match-weight decomposition in `core/explain.py`: per field `(level, m, u, log₂(m/u) bits)`, prior bits, total weight, posterior. Surface in `lineage.py` for probabilistic matchkeys; add an ASCII/markdown waterfall to `goldenmatch explain`. Gate: `explain --pair a,b` on an FS run shows per-comparison bits summing to the total; unit-tested.

## Phase 3 — Scale-out (substrate reuse)

- **3a. FS on bucket orchestration (no Rust)**: in `score_buckets._resolve_fast_path`, accept `mk.type == "probabilistic"` and resolve via the existing `_resolve_probabilistic_fast_path` (`probabilistic_fast.py`); use `score_probabilistic_vectorized` as the per-block compute. FS now rides bucket → and via `distributed/scoring.py` (already forces bucket) → Ray, and via the DataFusion/Sail spine. Gate: bucket vs polars-direct **cluster parity** on a synthetic FS run (extend `bench_fs_and_stages.py`); FS reaches the same N the weighted path does.
- **3b. Native FS kernel**: `score_block_pairs_fs_arrow` in `rust/extensions/native/src/score.rs` — args `levels[]`, `partial_thresholds[]`, flattened `match_weights[][]`, `min_weight`, `weight_range`, `prior_w`, `calibrated`; emits posterior-or-linear normalized score ≥ threshold. Reuses the same rayon/Arrow/exclude/`RAYON_MIN_PAIRS` scaffold as the weighted kernel. Python pure-fallback is `score_probabilistic_vectorized`. New native symbol → bump `pyproject.toml` + `Cargo.toml` in lockstep and republish per the `goldenmatch-native` wheel-skew rules. Gate: native vs numpy parity test; native 5× scoring on the wedge runner; FS 5M dedupe within the scale-envelope budget.
- **3c. Distributed FS validation**: 5M FS dedupe on 16c/64GB completes; F1 within tolerance of single-node on a labeled slice. (Mostly falls out of 3a once `EMResult` serializes across workers — Phase 1a.)

## Phase 4 — Calibration & accuracy analysis from labels

- Extend `evaluate` to consume FS match weights: ROC/PR + threshold→(P,R,F1) table + recommended cut; surface `probability_two_random_records_match` and m/u convergence charts in the report/dashboard. Gate: `goldenmatch evaluate` emits the threshold table and a recommended cut for an FS run.

---

## Sequencing

```
Phase 0  ██ 2-3 days        unblocks trust; no deps
Phase 1  █████ ~1 wk        train-once + labels; 1a unblocks 3c
Phase 2  ███ 3-4 days       no deps
Phase 3  ████████ ~1.5 wk   3a (days, no Rust) -> 3b (days, kernel) -> 3c (validation)
Phase 4  █████ ~1 wk        depends on Phase 1 labels
```

Full parity ≈ **2.5–3 weeks** because the distributed substrate is already paid for; the only new low-level work is one native kernel function (3b). Each phase lands as its own PR with a measured gate, CHANGELOG entry, and tests, per repo convention.

## Non-goals

- SQL-templated FS execution (Splink-on-DuckDB) — redundant given bucket+native+Ray; revisit only on a measured 50M+ FS-specific ask.
- Replacing the weighted/exact/embedding/LLM matchkey types — FS remains one opt-in option in the auto-config toolkit.
