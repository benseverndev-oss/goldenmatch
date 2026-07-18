# FS net-zero-evidence filter — kill scale-growing over-merge without a threshold move

Status: numpy reference SHIPPED (opt-in, default OFF); native port + default flip = follow-up.
Flag: `GOLDENMATCH_FS_REQUIRE_POSITIVE_EVIDENCE` (default OFF).

## Problem

The linear FS score is a min-max of the summed match weight `W` (the log-likelihood
ratio) into the achievable range `[pair_min_weight, pair_max_weight]`. Because that
range is ASYMMETRIC (a strong per-field disagree penalty pushes `pair_min` very
negative), a pair with `W <= 0` — i.e. the evidence does NOT favor a match (LR <= 1) —
can still map onto a score `>= 0.50` and be AUTO-LINKED. Such pairs agree only on the
(score-excluded) blocking field and disagree / are null on everything else; each one
chains two true clusters in union-find, so a handful explode into mega-clusters.

The over-merge GROWS with N (bigger blocks -> more net-zero pairs). Measured on the
synthetic person shape (`bench_er_headtohead/generate_fixture.py`, dupe_rate 0.20):

| n      | pair-F1 | precision | recall |
|--------|---------|-----------|--------|
| 5,000  | 0.918   | 0.848     | 1.000  |
| 30,000 | **0.405** | **0.254** | 0.9996 |

Recall is ~1.0 at both scales; the collapse is entirely precision.

## Why a global threshold move is the WRONG fix

Raising the link cut 0.50 -> 0.55 recovers the person shape (30K F1 0.979) but REGRESSES
the corpus, because a dataset's true partial matches score in different bands:

- person synthetic: true matches score WELL above 0.55 -> raising the cut costs no recall.
- `historical_50k` (real, corrupted PII): true partial matches straddle (0.50, 0.55] ->
  a 0.55 cut CUTS them (f1_probabilistic 0.826 -> 0.794).
- `ncvr_synthetic`: also over-merges at 0.50 and its real matches sit near 0.50.

A single threshold can never serve both — the discriminator is not scale, it's each
dataset's recall headroom above the cut.

## The fix: require strictly positive net evidence to LINK

The over-merge pairs are `W <= 0` (non-matches by Fellegi-Sunter); the real matches all
carry `W > 0` (some field agrees). So the principled, dataset-robust cut is: a pair links
only when `W > 0`, INDEPENDENT of where the asymmetric min-max places its score. This is
applied in the scorer's LINEAR branch by forcing `W <= 0` pairs below any cut. The
posterior calibration already folds the prior into the log-odds (0.99 Bayes cut), so the
filter is linear-only.

## Measured (numpy path, filter OFF -> ON, at the default 0.50 cut)

| dataset          | OFF (F1 / P / R)        | ON (F1 / P / R)         |
|------------------|-------------------------|-------------------------|
| person 30K       | 0.405 / 0.254 / 0.9996  | **0.979 / 0.959 / 0.9996** |
| historical_50k   | 0.826 / 0.926 / 0.746   | **0.826 / 0.926 / 0.746** (byte-identical) |
| ncvr_synthetic   | 0.861 / 0.757 / 0.9996  | **0.990 / 0.982 / 0.998**  |

It matches what the 0.55 threshold achieved on the person shape, does NOT touch
`historical_50k` (unlike the threshold), and FIXES `ncvr_synthetic`. Recall preserved
everywhere.

## Scope shipped now

`goldenmatch/core/probabilistic.py`, LINEAR mode, 3 numpy emit paths:
`score_probabilistic_vectorized`, `score_probabilistic_vectorized_batch`, and the scalar
`score_probabilistic`. `_fs_require_positive_evidence()` gates it; default OFF. Tests:
`tests/test_probabilistic_vectorized.py::TestRequirePositiveEvidence`.

## Completion (follow-up) — native + cross-surface + default flip

The DEFAULT FS route is the Rust `fs-core::score_fs_pair` kernel (native / wasm / DuckDB /
Postgres all share it), which does NOT yet carry the filter. To flip the default ON:

1. `fs-core::score_fs_pair` + `FsPairParams`: add `require_positive_evidence`; return a
   below-threshold sentinel when `!calibrated && require_positive_evidence && W <= 0`.
2. Thread the flag through the pyo3 kernel (`score_block_pairs_fs` / `_arrow`) and the
   Python native caller; the numpy + native paths pass `True`, keeping native==numpy
   parity. The wasm / DuckDB / Postgres constructors pass `False` for now (their parity
   fixtures stay byte-identical) until each surface opts in + regenerates its fixture.
3. Re-validate the full `autoconfig_quality` gate (native) + the person bench at scale,
   then flip `_fs_require_positive_evidence()` default to ON.
