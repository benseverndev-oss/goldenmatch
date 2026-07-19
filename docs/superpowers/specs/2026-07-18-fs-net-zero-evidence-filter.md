# FS net-zero-evidence filter — kill scale-growing over-merge without a threshold move

Status: SHIPPED, default ON — numpy AND the Rust `fs-core` kernel (native, the default
FS route). Cross-surface (wasm/DuckDB/Postgres) = follow-up.
Flag: `GOLDENMATCH_FS_REQUIRE_POSITIVE_EVIDENCE` (default ON; `0` restores legacy).

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

## Measured (filter OFF -> ON, at the default 0.50 cut)

person shape (numpy AND native — the over-merge is in the shared min-max scoring):

| n / path         | OFF (F1 / P / R)        | ON (F1 / P / R)         |
|------------------|-------------------------|-------------------------|
| 30K numpy        | 0.405 / 0.254 / 0.9996  | **0.979 / 0.959 / 0.9996** |
| 30K native       | 0.472 / 0.309 / 0.9999  | **0.987 / 0.974 / 0.9994** |

`autoconfig_quality` gate (native, default-on), vs the committed baseline:

| dataset (f1_probabilistic) | baseline | with filter |
|----------------------------|----------|-------------|
| historical_50k             | 0.8279   | 0.826 (OK, neutral) |
| ncvr_synthetic             | 0.9894   | **0.9901** (OK) |
| anchor_person_match        | 0.9607   | **1.0** (OK) |

Verdict PASS — it matches what the 0.55 threshold achieved on the person shape, does NOT
touch `historical_50k` (unlike the threshold, which dropped it to 0.794), and IMPROVES
`ncvr_synthetic` + `anchor_person_match`. Recall preserved everywhere.

## What shipped

LINEAR mode only (posterior folds the prior into the log-odds + a 0.99 Bayes cut).

- **numpy** (`goldenmatch/core/probabilistic.py`): the 3 emit paths
  (`score_probabilistic_vectorized`, `_vectorized_batch`, scalar `score_probabilistic`)
  drop `W <= 0` pairs.
- **Rust `fs-core::score_fs_pair`** (native, the default FS route): `FsPairParams` gains
  `require_positive_evidence`; returns the below-threshold sentinel `-1.0` when
  `!calibrated && require_positive_evidence && W <= 0`. Threaded through the pyo3 kernel
  (`score_block_pairs_fs` / `_arrow`) + the Python native caller, so **native == numpy
  under the flag** (parity asserted in `TestNativeFSParity`). Wheel-skew safe: the caller
  passes the kwarg only when the wheel exports `FS_SUPPORTS_REQUIRE_POSITIVE_EVIDENCE`, so
  an older wheel degrades to the legacy native behavior instead of raising.
- `_fs_require_positive_evidence()` gates both; **default ON**. Tests:
  `tests/test_probabilistic_vectorized.py::TestRequirePositiveEvidence` +
  `TestNativeFSParity`.

## Follow-up — the other surfaces

The `fs-wasm` (TS) / DuckDB / Postgres surfaces share `score_fs_pair` but pass
`require_positive_evidence = false` (their cross-surface parity fixtures are byte-locked).
Each opts in by passing `true` + regenerating its fixture — a per-surface follow-up,
tracked against the every-capability-on-every-surface north star.
