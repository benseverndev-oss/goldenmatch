# FS native `missing="disagree"` — parity verification

**Date:** 2026-07-25
**Kernel:** `goldenmatch-native` with `FS_SUPPORTS_MISSING_DISAGREE` (branch `feat/fs-native-disagree-mode`)
**Dataset:** `historical_50k` (50,578 rows, null-heavy → auto-config resolves `missing="disagree"`)

## Question

The native FS kernel gained `missing="disagree"` support so `historical_50k`
runs on the Rust kernel instead of the numpy fallback (~3× faster). The native
output is not byte-identical to numpy: a fraction of multi-member partitions
differ. Is that churn a **bug in the disagree weight math**, or the **inherent
rust-rapidfuzz vs py-rapidfuzz scoring tolerance** on observed fields that also
exists in the textbook `unobserved` mode?

## Method

`scripts/verify_disagree.py`. For each `missing_mode ∈ {disagree, unobserved}`,
run the same auto-configured probabilistic dedupe with the native kernel ON and
the numpy scorer (`GOLDENMATCH_FS_NATIVE=0`) — holding blocking constant (the
mode is pinned via `GOLDENMATCH_FS_MISSING`; auto-config is deterministic on
identical data) — and compare per-record partition assignment. Each
(mode, backend) runs in its own subprocess so the native / missing-mode env
gates are read fresh. Churn = symmetric difference of the exact multi-member
partition sets (any single-record difference marks the whole partition changed —
a strict metric).

A disagree-specific weight bug would show churn in `disagree` but ≈none in
`unobserved` (where the disagree branch is never taken). Inherent scoring
tolerance shows comparable churn in both.

## Result

| mode | multi-member partitions (native / numpy) | identical | churn | churn % |
|---|---|---|---|---|
| disagree   | 4071 / 4081 | 3983 | 186 | **4.46%** |
| unobserved | 3582 / 3588 | 3422 | 326 | **8.70%** |

## Verdict: PASS — inherent tolerance, not a disagree bug

The native↔numpy churn in **`unobserved` mode (8.70%) is ~2× higher** than in
`disagree` mode (4.46%). A disagree-specific bug would make disagree churn
*exceed* unobserved; the opposite holds. The divergence is entirely the
rust-vs-py rapidfuzz scoring tolerance on observed fields — present in both
modes, and the disagree evidence-against math is if anything *more* stable
across backends than the baseline `unobserved` path.

Corroborating evidence that the disagree math itself is exact:
- `fs-core` unit test `score_fs_pair_missing_disagree_adds_level0_weight`
  asserts the exact level-0 weight contribution (8/18 vs 12/18).
- The min–max weight range is computed identically on both sides.
- The FS test suite (`test_probabilistic.py`, `test_native_parity.py`,
  `test_fs_autoconfig_v2.py`) passes with native on: **237 passed**.

Under the "Rust is the reference" posture (goldenmatch-native reference-mode
gate), this within-tolerance divergence is acceptable — the same posture already
governs every native FS scorer path.

## Reproduce

```
uv sync --all-packages
.venv/bin/python scripts/build_native.py          # builds the disagree kernel
GOLDENMATCH_AUTOCONFIG_MEMORY=0 ARROW_DEFAULT_MEMORY_POOL=system \
  .venv/bin/python scripts/verify_disagree.py      # needs goldenmatch[bench] for splink_datasets
```
