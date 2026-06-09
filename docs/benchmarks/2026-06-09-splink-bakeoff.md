# ER bake-off: GoldenMatch (zero-config + probabilistic auto-config) vs hand-rolled Splink

**Date:** 2026-06-09
**Commit:** `970aab61` (deterministic EM training-pair sampling, #829)
**Runner:** `large-new-64GB` (16c / 64 GB, Linux) — single Linux runner for fairness
**Workflow:** `bench-probabilistic.yml` (`run_bakeoff=true`), run `27217168831`
**Harness:** `scripts/bench_er_headtohead/run_bakeoff.py` (3 engines, each self-timed in its own subprocess)

## What this measures

A fair autoconfig-vs-hand-rolled comparison: expert hand-tuned Splink against
GoldenMatch with **zero tuning**, recording both accuracy and performance on the
ER benchmark datasets. Three engines per dataset:

1. **gm_zeroconfig** — `dedupe_df(df)` with no config (the controller auto-picks
   weighted/exact/probabilistic). Its controller overhead / RED-refuse behavior is
   reported, not hidden.
2. **gm_probabilistic** — `auto_configure_probabilistic_df(df)` then
   `dedupe_df(df, config=...)`. Like-for-like vs Splink (both Fellegi-Sunter),
   zero tuning.
3. **splink** — the per-dataset expert hand-rolled configs in
   `run_splink.py::_SETTINGS_BY_DATASET` (compound blocking + JaroWinkler /
   DamerauLevenshtein / ExactMatch comparisons + EM). Genuinely hand-tuned, reused
   as-is. Splink honestly skips `dblp_acm` (out of its tuned domain).

All accuracy comes from ONE shared evaluator (`evaluate.evaluate`) over the same
string `record_id` key space, so all three engines are judged by identical code.

## Why this run is the number of record (the determinism fix)

The earlier published figures rested on a **non-reproducible measurement**. On the
pre-fix commit `45e17ed4`, three invocations of the *identical* `gm_probabilistic`
path on `historical_50k` — same runner, same CI run — gave F1 of **0.805**
(single-config panel), **0.779** (v1-vs-v2 panel), and **0.643** (bake-off): a
0.16 F1 spread across byte-identical code. Root cause: `_sample_blocked_pairs`
drew the EM training sample by seeded-shuffling bare block indices, but the blocks
arrived in a non-deterministic order (parallel / hash-bucketed construction), so
the seeded shuffle still permuted a different order run-to-run → different m/u
weights → different threshold → different P/R.

#829 sorts blocks by their stable `block_key` (and row_ids within a block) before
the seeded shuffle. Post-fix, the three harnesses agree on `historical_50k` within
**0.002** (bake-off 0.7782, single-config panel 0.7783, v1-vs-v2 panel 0.7804) and
agree exactly on `dblp_acm` (0.3768). The numbers below are from the deterministic
commit and reproduce run-to-run.

## Results (deterministic)

### historical_50k (50,003 records — Splink's flagship dataset)

| Engine | P | R | F1 | B3-F1 | wall(s) | peak RSS(MB) | throughput pairs/s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gm_zeroconfig | 0.553 | 0.445 | 0.493 | 0.640 | 52.27 | 1358.1 | 6747 |
| gm_probabilistic | 0.812 | 0.747 | **0.778** | **0.844** | 60.83 | 1025.3 | 3289 |
| splink | 0.966 | 0.623 | 0.757 | 0.789 | 3.22 | 1048.6 | 47552 |

GM-prob beats Splink pairwise (+0.021) and at cluster level (B3 +0.055). Splink is
~19x faster.

### febrl3 (synthetic PII, single-source)

| Engine | P | R | F1 | B3-F1 | wall(s) | peak RSS(MB) | throughput pairs/s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gm_zeroconfig | 0.942 | 0.993 | 0.967 | 0.989 | 26.17 | 711.5 | 484 |
| gm_probabilistic | 1.000 | 0.982 | **0.991** | 0.995 | 10.65 | 711.5 | 584 |
| splink | 0.998 | 0.935 | 0.965 | 0.980 | 1.89 | 964.2 | 3099 |

GM-prob beats Splink pairwise (+0.026). Splink ~6x faster.

### synthetic_person

| Engine | P | R | F1 | B3-F1 | wall(s) | peak RSS(MB) | throughput pairs/s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gm_zeroconfig | 0.816 | 0.955 | 0.880 | 0.978 | 3.73 | 714.5 | 766 |
| gm_probabilistic | 0.998 | 0.997 | **0.998** | 1.000 | 4.62 | 714.5 | 267 |
| splink | 1.000 | 0.993 | 0.996 | 0.999 | 1.57 | 714.5 | 779 |

GM-prob edges Splink pairwise (+0.001). Splink ~3x faster.

### dblp_acm (bibliographic — Splink skips)

| Engine | P | R | F1 | B3-F1 | wall(s) | peak RSS(MB) | throughput pairs/s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gm_zeroconfig | 0.065 | 0.970 | 0.122 | 0.686 | 12.36 | 714.5 | 2715 |
| gm_probabilistic | 0.639 | 0.267 | 0.377 | 0.791 | 1.01 | 714.5 | 748 |
| splink | (skips) | - | - | - | - | - | - |

**Not a Splink comparison** — Splink's hand-rolled spec skips bibliographic data.
The `gm_probabilistic` pairwise F1 here is weak (0.377; threshold too high → low
recall 0.267, though cluster-level B3 is 0.791). The probabilistic auto-config is
not tuned for bibliographic shape; the **zero-config weighted path** is the right
tool for `dblp_acm` (0.964 F1, see the README zero-config controller table). This
corrects an earlier published `dblp_acm = 0.879` figure, which was a
non-deterministic lucky draw that does not reproduce.

## Verdict

On every dataset Splink scores, GoldenMatch's zero-tuning probabilistic auto-config
**matches or beats** the hand-rolled Splink config on pairwise F1 under the shared
evaluator:

| Dataset | GM-prob F1 | Splink F1 | ΔF1 |
| --- | --- | --- | --- |
| historical_50k | 0.778 | 0.757 | +0.021 |
| febrl3 | 0.991 | 0.965 | +0.026 |
| synthetic_person | 0.998 | 0.996 | +0.001 |

## Honest framing

- Pairwise P/R/F1 and B-cubed (cluster) F1 come from ONE shared evaluator, so all
  three engines are judged by identical code.
- The often-cited **~0.97** Splink figure on `historical_50k` is a *cluster/entity*
  metric, NOT exhaustive within-cluster pairwise F1. Under this shared harness
  Splink scores ~0.757 *pairwise* on `historical_50k` (recall-bound: 5156 clusters,
  mean size ~10, no single field exceeds 0.60 recall → ~0.93 pairwise ceiling for
  any engine). The honest claim is "matches/beats Splink on the same evaluator,"
  not "0.97 pairwise."
- **Splink is faster** on every dataset (3-19x), and retains distributed
  Fellegi-Sunter at 1B+ rows on Spark plus the mature interactive m/u
  comparison-viewer UI. GoldenMatch's edge is zero tuning and accuracy parity.
- Performance (wall / peak RSS / throughput) is SINGLE-RUN per engine and subject
  to runner variance — treat ratios as directional, not exact. Accuracy is
  deterministic as of #829.
- Splink skipping `dblp_acm` is recorded as a skip, never scored 0.
