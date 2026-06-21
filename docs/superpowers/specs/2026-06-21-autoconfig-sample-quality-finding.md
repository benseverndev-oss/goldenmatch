# Auto-config sample-size quality finding (Stage D, pure-Python lever)

- **Date:** 2026-06-21
- **Bench:** `scripts/bench_autoconfig_sample_quality.py` (pure Python, no native ext)
- **Context:** validates the "bigger samples changes the calculus" hypothesis behind
  the native auto-config port (spec `2026-06-20-autoconfig-native-core-design.md`).

## Question

Auto-config measures blocking on a SAMPLE and linearly extrapolates the candidate
pair count to full row count (`BlockingProfile.extrapolate_to`: `pairs *=
n_full/n_sample`). The v3 planner picks the backend rung off that
`estimated_pair_count` (50M / 5B boundaries). Does a bigger profiling sample
materially improve the estimate?

## Result (MEASURED)

100k-row synthetic person frame, two hand-built blocking schemes, samples 1%-50%,
3 seeds each. `extrap/true` = extrapolated-from-sample pair count / true full-frame
pair count:

| sample frac | exact(last_name) | soundex(last_name) |
|---|---|---|
| 1%  | 0.010 | 0.010 |
| 2%  | 0.019 | 0.019 |
| 5%  | 0.048 | 0.048 |
| 10% | 0.098 | 0.098 |
| 20% | 0.197 | 0.198 |
| 50% | 0.497 | 0.497 |

Spread across seeds: 0.002-0.012 (tight). soundex true pairs = 243,087,982.

**`extrap/true` tracks the sampling fraction almost exactly, on both schemes.**

## Mechanism

Within-block candidate pairs grow QUADRATICALLY with block size
(`sum(s*(s-1)/2)`), but `extrapolate_to` scales the sample's pair count only
LINEARLY (`* n_full/n_sample`). In the regime where block size grows with N (fixed
key cardinality, which is the common ER case), the sample's pairs are `~frac^2` of
the full count, so linear extrapolation recovers only `frac * true` -- a
systematic under-estimate by the sampling fraction:

- 1% sample  -> ~100x under-estimate
- 20% sample -> ~5x under-estimate
- 50% sample -> ~2x under-estimate
- 100% (full measurement) -> accurate

(A separate small-sample INSTABILITY also exists: an auto-config-picked
`soundex(zip)` pass -- degenerate on numeric zips -- collapsed a 1k sample into one
giant block, spiking the estimate 8x OVER. So tiny samples are not just biased but
unstable.)

## Planner consequence

The controller's `ControllerBudget` samples sqrt-scaled, capped at 20k. On a 10M-row
dataset that is a 0.2% sample -> a true 60M-pair dataset (chunked rung) reads as
~0.12M pairs -> the planner picks `simple/bucket`, **under-provisioning the backend
by ~500x.** This is the same family of failure as the at-scale blocking blow-ups
(`project_autoconfig_715_blocking_refuse`, `feedback_reproduce_at_scale_before_designing`).

## So what -- the actionable conclusion

1. The "bigger samples" lever is REAL and the error is SEVERE.
2. BUT bigger-sample extrapolation only partially helps: a 50% sample is still 2x
   off. The accurate fix is FULL-FRAME blocking measurement (`extrap/true = 1.0`),
   not merely a larger sample.
3. The hook already exists: at `planning_effort in {thinking, einstein}` the
   controller calls `measure_blocking_profile(df, config)` for measured full-frame
   pair counts instead of extrapolating. **Native-fast profiling is what makes
   full-frame measurement cheap enough to be the default** -- that is the real
   config-quality payoff of the native port, sharper than "raise the sample size".
4. Next step (when native + CI are green): bench the WALL of native full-frame
   `measure_blocking_profile` vs the Python path at 1M/10M rows; if affordable,
   propose flipping full-measurement on at lower planning-effort tiers. Gate any
   default change on that measured wall (measure-first; `feedback_verify_perf_not_just_ship`).

## Stage D bench UPDATE (2026-06-21, MEASURED — partly refutes the native premise)

Bench: `scripts/bench_stage_d_full_frame_measure.py` (native ext present). Person
frame, single exact `last_name` pass (fixed-cardinality — the regime where
extrapolation breaks). Wall of `measure_blocking_profile` (the current production
full-frame path) vs a restructured "fast" path (one `group_by().len()` →
pair-count aggregate):

| N (rows) | `measure_blocking_profile` | fast (1 groupby + native agg) | fast (pure-py agg) |
|---|---|---|---|
| 100k | 120 ms | 20 ms | 6 ms |
| 1M   | 272 ms | 19 ms | 19 ms |

High block-cardinality probe (native vs pure `candidate_pair_count` over the
block-sizes list, the only step the native kernel touches):

| N | n_blocks | native agg | pure-py agg | speedup |
|---|---|---|---|---|
| 1M | 317k | 5.1 ms | 16.2 ms | 3.2× |
| 5M | 1.58M | 91.4 ms | 79.2 ms | **0.9× (native slower)** |

**Conclusions (honest, measure-first):**
1. **Full-frame measurement is ALREADY affordable** — 272 ms at 1M with the
   current implementation. It can be defaulted-on at lower planning-effort tiers
   TODAY, with NO native acceleration. The finding's framing ("native-fast
   profiling is what makes full-frame measurement cheap enough to be the default")
   is **not borne out** — full-frame is already cheap with pure polars.
2. **The real win is a POLARS refactor, not native.** `measure_blocking_profile`'s
   per-block `collect()` loop dominates (272 ms@1M); a single `group_by().len()`
   gets the block-size distribution in ~19 ms (**≈14×**). This restructure applies
   verbatim to the TS port's profiler too (all surfaces benefit).
3. **The native `candidate_pair_count` kernel is NOT the lever here.** The
   size→pairs sum is trivial: 3× at moderate block counts, and a wash-to-LOSS at
   1.5M+ blocks (marshaling a million-element list to the kernel beats the Python
   `sum`). Keep it off this path.

**Revised actionable conclusion:** the config-quality fix is *doing full-frame
measurement at all* (cheap; flip it on at lower tiers) + the ~14× polars
restructure of `measure_blocking_profile` — NOT native-accelerating it. The native
core's payoff stays cross-surface parity + planner/classifier correctness.
