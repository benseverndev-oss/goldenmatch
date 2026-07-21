# Ensemble scorer: re-opening the "kernelize it regresses recall" measurement

**Date:** 2026-07-21
**Status:** Measured. Safe opt-in kernel landed; default-flip + native-id (metric 14/19 → 15/19)
deferred pending a large-block perf eval (see Decision).

## Background — the deferral this re-opens

`ensemble` (`max(jaro_winkler, token_sort/100, soundex·0.8)`) was the one string scorer marked
**`declined`** in the `scorer_kernels` coverage manifest (`parity/goldenmatch.yaml`). The decline note
(`backends/score_buckets._resolve_score_pair_callable`) said:

> a per-pair reimpl measurably regressed Febrl3 recall (0.922 → 0.782); the float32 matrix ensemble is
> the source of truth, so it stays off the bucket/fast path. Kernelizing means re-opening that
> measurement.

This document re-opens it.

## The two ensemble scoring paths

- **Matrix path** (`core/scorer._fuzzy_score_matrix`, the *source of truth*): builds each component as a
  native/rapidfuzz `cdist` matrix, casts every component to **float32**, `np.maximum`-combines. The
  weighted-matchkey combine downstream (`find_fuzzy_matches`) is also float32.
- **Bucket fast path** (`_resolve_score_pair_callable` → per-pair callable): scores each pair, combines
  the weighted matchkey in **float64**. Ensemble *declined* here (returned `None`), so an ensemble
  matchkey fell back to the matrix path even on `backend="bucket"`.

The historical regression came from a **float64** per-pair reimpl diverging from the **float32** matrix
at the `>= threshold` boundary.

## What the measurement found (goldenmatch 3.6.0, autoconfig-v2)

Harness: `scripts/dqbench_adapters/febrl3.evaluate_febrl3` over true zero-config `auto_configure_df`
(5000 rows, 6538 GT pairs). Febrl3 auto-config assigns `ensemble` to 3 fields (address_1, address_2,
suburb) inside a weighted matchkey. Kernel exercised (instrumented): **664 calls**.

| Path | TP | FP | FN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|
| baseline (decline → float32 matrix) | 6429 | 5 | 109 | 0.9992 | 0.9833 | 0.9912 |
| **float64** per-pair kernel | 6429 | 5 | 109 | 0.9992 | 0.9833 | 0.9912 |
| **float32-cast** per-pair kernel | 6429 | 5 | 109 | 0.9992 | 0.9833 | 0.9912 |

**Byte-identical TP/FP/FN across all three.** The old 0.922 → 0.782 regression **does not reproduce.**

### Why the old regression was a bug, not the dtype

Field-level, over 719,400 real (address/name) pairs vs the matrix source of truth:

| Kernel | exact-equal vs matrix | max \|diff\| | threshold-crossings (0.80/0.85/0.90) |
|---|---|---|---|
| float64 | 5.38% | **2.98e-8** | 31 / 719,400 = **0.0043%** |
| **float32-cast** | **100.00%** | **0.0** | **0** |

A float64-vs-float32 divergence is bounded by ~**3e-8** (float32 ULP near 1.0). A 14-point recall drop
(0.922 → 0.782) **cannot** come from 3e-8 rounding — the old per-pair reimpl must have had a real bug
(wrong `token_sort` variant, wrong soundex bonus, or a missing `/100` scale). A faithful mirror of the
vetted `score_field` scalar twin (this doc's kernels) does not have it.

### The float32-cast kernel is provably byte-identical to the matrix

`float32(max(a, b, c)) == max(float32(a), float32(b), float32(c))` because the float32 cast is monotonic
and therefore commutes with `max` (the arg-max is preserved; ties cast equally). The matrix computes the
RHS (`np.maximum` over three `astype(np.float32)` components); the safe per-pair kernel computes the LHS.
So the float32-cast kernel **cannot flip any `>= threshold` decision the matrix makes** — the exact
field-level parity bar the decline demanded. Asserted in `tests/test_ensemble_kernel_parity.py`.

## What landed

- `core/scorer._ensemble_score_single` (float64) + `_ensemble_score_single_f32` (float32-cast).
- `_resolve_score_pair_callable` gains an **opt-in** `GOLDENMATCH_ENSEMBLE_KERNEL`:
  - unset / `0` / `off` → **decline** (default; byte-identical to before this change).
  - `1` / `on` / `f32` → the **safe** float32-cast kernel (field-exact with the matrix).
  - `f64` → the divergent float64 twin (measurement A/B only).
- `tests/test_ensemble_kernel_parity.py`: field-level parity (f32-cast == matrix; f64 within ULP).
- Manifest: the `ensemble` deferral reason updated from "declined (regresses recall)" to reflect the
  re-measurement (regression gone; safe f32-cast kernel exists; opt-in pending the perf eval below).

## Decision — why opt-in, not default-on (yet)

Recall is **not** the blocker anymore (proven above). The remaining question is **large-block perf**: the
bucket fast path scores ensemble **per-pair in Python** (the `_score_block_vec` float32 vec lane does not
yet cover ensemble), whereas the matrix path uses vectorized `cdist`. For the 5M/25M ensemble configs the
decline note protects, per-pair Python could regress wall time. So:

- **Default stays decline** (matrix path) until either (a) a scale bench shows the per-pair kernel is not
  a wall regression on large ensemble blocks, or (b) ensemble is added to the **float32 vec lane**
  (`_VEC_SUPPORTED` + `_vec_field_matrix`), which is vectorized AND byte-identical to the matrix — the
  clean default-on path.
- **Metric (14/19 → 15/19):** counting ensemble as kernel-backed requires it in the *native* arrow-block
  kernel (`_NATIVE_SCORER_IDS` / a `score_one` id or an fs-core-style composed id), not just a Python
  per-pair callable. `score_one(2)` token_sort already matches rapidfuzz to float32 (0 crossings), so a
  native composed ensemble is feasible; it's deferred with the vec-lane work as one follow-on.

## Reproduce

Field-level parity (no data download):

```
pytest packages/python/goldenmatch/tests/test_ensemble_kernel_parity.py
```

End-to-end Febrl3 (needs `recordlinkage`, which ships the dataset) — score the same
`scripts/dqbench_adapters/febrl3.evaluate_febrl3` closure the `test_bucket_febrl3_parity` test uses,
under each `GOLDENMATCH_ENSEMBLE_KERNEL` mode:

```python
import os, goldenmatch as gm
from dqbench_adapters.febrl3 import evaluate_febrl3, load_febrl3_df_and_gt   # scripts/ on sys.path
from goldenmatch.core.autoconfig import auto_configure_df
os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"
df, gt = load_febrl3_df_and_gt()
def dd(frame):
    cfg = auto_configure_df(frame)
    for mk in cfg.get_matchkeys():
        if getattr(mk, "rerank", None): mk.rerank = False
    cfg.backend = "bucket"
    return gm.dedupe_df(frame, config=cfg)
# unset / =f32 / =f64 all give TP=6429 FP=5 FN=109 (P=0.9992 R=0.9833 F1=0.9912)
print(evaluate_febrl3(df, gt, dd))
```
