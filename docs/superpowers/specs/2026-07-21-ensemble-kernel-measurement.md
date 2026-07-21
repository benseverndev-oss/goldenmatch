# Ensemble scorer: re-opening the "kernelize it regresses recall" measurement

**Date:** 2026-07-21
**Status:** SHIPPED. Native `score_one` id 12 kernel (metric **14/19 → 15/19**) + ensemble
**default-on** (`GOLDENMATCH_ENSEMBLE_KERNEL=1`), made safe by the fast-path perf guard below.

> **UPDATE 2 (2026-07-21, supersedes UPDATE 1 and the "DEFAULT-ON" sections below) — default-on
> RE-ENABLED via a source fix.** UPDATE 1's "nested ThreadPoolExecutor" framing was imprecise.
> `rapidfuzz.cdist` defaults to `workers=1` (sequential), so there is no inner thread pool. The real
> cause, found by INSTRUMENTING the workload (`GOLDENMATCH_BUCKET_DEBUG=1`): making `ensemble`
> resolvable flips a **name-scorer matchkey** onto `score_buckets`' fast path, and its
> `name_freq_weighted_jw` / `given_name_aliased_jw` fields are neither vec-supported nor native-id, so
> they run the **O(N²) per-pair Python loop** in `_score_one_bucket_fast`. MEASURED on the same 65 NCVR
> blocks: fast path **26.97 s** vs `find_fuzzy_matches` (vectorized) **1.40 s** — a **19×** slowdown that
> blows the 120 s per-test timeout on a slow CI runner (→ `os._exit`, the "hang"). **Fix:** a PERF GUARD
> in `_resolve_fast_path` (following the existing NE-parity-decline precedent) declines the fast path
> whenever a field's scorer is neither in `_VEC_SUPPORTED` nor `_NATIVE_SCORER_IDS` (i.e. would force
> per-pair Python) → routes to the vectorized `find_fuzzy_matches`. Verified locally: default-on
> bucket_score dropped **26.97 s → 1.31 s**, output identical (918 pairs / 65 blocks). All-native and
> all-vec matchkeys keep the fast path; ensemble is now default-on and the 1.47× Febrl3 win stands.
> (This is a general fast-path fix — any name-scorer matchkey now avoids the slow per-pair path.)

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

## What landed (v1, #1992 — opt-in)

- `core/scorer._ensemble_score_single` (float64) + `_ensemble_score_single_f32` (float32-cast).
- `_resolve_score_pair_callable` gained an **opt-in** `GOLDENMATCH_ENSEMBLE_KERNEL` (decline by default).
- `tests/test_ensemble_kernel_parity.py`: field-level parity (f32-cast == matrix; f64 within ULP).

## Update (v2, 2026-07-21 — DEFAULT-ON via the vec lane)

The v1 "why opt-in" hesitation was **large-block perf**: the fast path scored ensemble per-pair in
Python, and a single-block micro-bench showed the vec lane slower than the native-accelerated
`find_fuzzy_matches` matrix for blocks ≥256. But the decisive number is the **end-to-end wall**, and it
says the opposite:

**Febrl3 dedupe wall (median of 4, config pre-built so auto-config is excluded):**

| Path | wall | vs decline |
|---|---|---|
| kill-switch (`=0`, decline → float32 matrix) | 10.98s | — |
| **DEFAULT-ON** (fast path) | **7.46s** | **1.47× faster** |

Recall is byte-identical (TP=6429 FP=5 FN=109 either way). The end-to-end win is because declining an
ensemble field routes the **entire weighted matchkey** through `find_fuzzy_matches` (which allocates a
full N×N float32 matrix per block for *every* field); making ensemble fast-path-eligible keeps the whole
matchkey on the streaming fast path. On Febrl3 the ensemble fields sit in a *mixed* matchkey (with
`given_name_aliased_jw` / `name_freq_weighted_jw`, not vec-supported), so the win comes from the per-pair
fast path, not the vec lane — the vec lane is a further speedup for the rarer all-vec-supported ensemble
matchkey.

Landed:
- `ensemble` added to `_VEC_SUPPORTED` + a float64 `_vec_field_matrix('ensemble')` case (byte-identical to
  the per-pair `_ensemble_score_single`; the vec parity test + a soundex-empty-code edge test cover it).
- `_resolve_score_pair_callable` ensemble is **fast-path eligible by DEFAULT**; `GOLDENMATCH_ENSEMBLE_KERNEL=0`
  (`off`/`false`) is the **kill-switch** restoring the historical decline.

**The one shape where decline is still faster** (documented, not fixed): a huge *all-vec-supported*
ensemble block (≥ a few hundred rows), where the vec lane's three float64 `cdist` + `maximum` lose to
`find_fuzzy_matches`'s native-accelerated float32 components (micro-bench: 0.67× at n=2000). Rare — such
blocks are bounded by `skip_oversized`/`max_block_size`, and the representative end-to-end is a clear win —
and the kill-switch covers it. A native arrow-block ensemble id would close both this and the metric.

- **Metric (14/19 → 15/19):** still NOT counted. The default-on fast path uses a Python composite
  (rapidfuzz + jellyfish + numpy), not a native `score_one`/arrow-block kernel, so ensemble stays in
  `scorer_kernels_deferred`. `score_one(2)` token_sort already matches rapidfuzz to float32 (0 crossings),
  so a native composed ensemble id is feasible — the remaining follow-on for the metric bump.

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
# default-on (unset) and kill-switch (=0) both give TP=6429 FP=5 FN=109
# (P=0.9992 R=0.9833 F1=0.9912); default-on's dedupe wall is ~1.47x faster.
print(evaluate_febrl3(df, gt, dd))
```
