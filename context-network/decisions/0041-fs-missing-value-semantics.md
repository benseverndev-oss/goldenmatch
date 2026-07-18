# 0041 — Fellegi-Sunter missing-value semantics: `unobserved` vs `disagree`, chosen per-dataset

**Status:** Accepted. **Shipped:** goldenmatch 3.4.0 (PRs #1834 establishing, #1851 correcting, #1872 native routing; ride-alongs #1856 / #1861 / #1830).

## Context

The Fellegi-Sunter scorer had no principled treatment of a **missing** field
comparison — a null on either side of a pair. It counted as a level-0
*disagreement*, which is only correct when missingness is itself evidence
against a match. #1834 (closes #1819) rewrote this to the textbook stance:
a missing comparison carries **no evidence** ("unobserved"). Concretely
`comparison_vector` returns `-1` for a field unobserved on either side,
the discrete/continuous EM likelihoods **exclude** those fields, and the linear
score normalizes **only over observed fields** (neutral 0.5 when a pair supplies
no evidence at all). Applied uniformly across Python scalar/vectorized/batched,
TypeScript, and the Rust native/fused kernels; persisted EM models bumped to
**schema v2** (v1's null-as-disagreement meaning is now wrong and is rejected),
gated by `goldenmatch-native 0.1.17`.

That single stance regressed `historical_50k` F1 **0.83 → 0.33**: its
missingness is *informative* (8.9–50% null across fields), so a pair agreeing on
its few populated fields looks certain and the dataset mass-merges. Neither
semantics is universally right — "unobserved" is correct for missing-at-random,
"disagree" for missing-not-at-random — and the library has no labels to know
which a dataset is.

## Decision

**The library does not impose one missing-value semantics; it exposes both and
auto-config picks per dataset.**

1. **Config contract.** `MatchkeyConfig.missing ∈ {"unobserved", "disagree",
   None}`, default `None → "unobserved"` (existing configs byte-unchanged).
   `disagree` forces a missing comparison back to level 0 (evidence against);
   `unobserved` leaves the pair's other fields as the only evidence. Global
   override `GOLDENMATCH_FS_MISSING` (env beats config, same shape as
   `GOLDENMATCH_FS_CALIBRATED` / `_MONOTONIC`). **Clean corpora are unchanged by
   construction** — the toggle only touches missing comparisons; observed pairs
   score identically under both modes (`test_both_present_is_identical_under_both`).
2. **Auto-config selection.** `_pick_missing_semantics` reads the profiled
   `null_rate` of the **comparison** fields and picks `disagree` at **≥ 20%**,
   using `max` across fields (one heavily-null field makes missingness
   informative; averaging lets clean fields mask it). The 20% cut is
   *calibrated* (it only has to separate `historical_50k` from febrl3/ncvr), not
   derived — null rate is a proxy for missing-not-at-random, which is the real
   condition. The two escape hatches exist because a null-heavy-but-random
   dataset would be mis-picked.
3. **Native declines what it cannot express.** The native FS kernel implements
   only the neutral/`unobserved` semantics (`FS_SUPPORTS_MISSING_NEUTRAL` — a
   null field is skipped). Under `disagree` it over-matched and collapsed
   precision (P 0.94 → 0.24), so `_fs_native_eligible` **declines the native
   path** whenever `fs_missing_mode(mk) == "disagree"` and routes to numpy,
   which honors both modes (#1872). This is the reference-mode posture in
   [0042](0042-native-kernel-owns-fs-coverage.md): the fast path declines rather
   than silently producing a different answer; numpy is the lossy-but-complete
   fallback.

## Consequence

- **These bugs are recall-only and silent** — no error, no warning, and
  "byte-identical clusters" parity is true and useless (Python + Rust were
  identically wrong). Both #1834's regression and the #1835 EM regression
  merged **red** because `quality_gate` was non-blocking and (for #1834) its
  path filter did not even list `probabilistic.py` — see
  [0045](0045-quality-gate-required-watches-own-surfaces.md).
- **Ride-along missing-value bugs of the same class:** #1856 — the weighted
  scorer accumulated `weight_sum` over observed fields but divided by
  `total_weight`, so a null field capped a pair below threshold (a null `dob`
  capped at 0.70 under a 0.85 cut); fixed to divide by **observed** weight
  (ER-bench 100K recall 0.8751 → 0.9295). #1861 — two splink-upgrade calibration
  sites summed `match_weights[name][vec[k]]` unguarded, and `vec[k] == -1`
  indexes `weights[-1]`, the **maximal-agreement** weight, so a null field added
  the maximum evidence *for* a match; blast radius is the Splink-conversion path
  only (`_regular_weight_sum` skips `vec[k] < 0`).
- **The blocking-field EM invariant (#1835 → #1836).** #1835 let configured
  blocking fields become EM-learned in multi-pass configs; a near-unique
  blocking key (`postcode_fake`) never agrees among random pairs, its `u`
  collapses to the smoothing floor, and `log2(m/u)` explodes to ~28 bits and
  dominates the score (F1 0.83 → 0.57). #1836 folds `blocking_fields` back into
  `always_conditioned` so blocking fields always take the fixed prior —
  **superseding** #1835's weight-learning (its only benefit was on a clean
  synthetic unit test; it regresses real corrupted PII).
- **Parked:** under `unobserved`, min-max normalization over observed fields can
  let a pair agreeing on its single observed field normalize to 1.0 (max
  confidence from minimal evidence). Fixing it alone moves `historical_50k`
  0.33 → 0.59 but costs febrl3 and is inert once auto-config routes null-heavy
  data to `disagree`. Parked on `fix/1846-normalization-range` (#1854, under the
  #1859 umbrella).
