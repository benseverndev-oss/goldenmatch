# #2633 — Reach `title + year`-shaped compounds on a share-skewed, size-safe blocking key

**Status:** design proposal, NOT approved, NOT implemented
**Date:** 2026-09-04
**Issue:** https://github.com/benseverndev-oss/goldenmatch/issues/2633
**Verified against:** `origin/main` @ `53780971e` (2026-09-04) — the issue's diagnosis,
last updated 2026-08-25, still matches current code at every file:line cited below.
Nothing in the 10 days between has touched `_build_compound_blocking`,
`_is_admissible`, `_all_single_oversized`, or the `blocking_skewed` rule table.

## Why (synthesized from the issue thread — nothing here is new measurement)

On DBLP-ACM, `__title_key__` alone commits with `total_comparisons=33,563` for
2,224 true pairs (ceiling 0.064) at recall 0.9717. Adding `year` as a second
blocking component drops candidates to 5,749 at **identical** recall — `year`
is free selectivity because all 2,224 ground-truth pairs share it.

Two gates, confirmed still both in the shape the issue found them:

1. **Gate 1 (component admissibility) — already fixed, in `main` since #2727.**
   `_is_admissible` (`autoconfig.py:2623-2658`) no longer vetoes `numeric`/`date`
   columns outright; they pass through the same measured guards (`_max_block_size
   > 1`, `not _is_perfect_surrogate`, `_nonnull_ratio < max`) as `zip`. This also
   means the "surrogate-key guard becomes required" concern from the issue's
   first comment is already handled — `_is_perfect_surrogate` still rejects an
   `id`-shaped column even after the type veto is gone. **No work needed here.**

2. **Gate 2 (reachability) — still shut, this is the actual blocker.**
   `_build_compound_blocking` is only ever called when every single-column key
   is oversized (`autoconfig.py:4479-4496`, `_all_single_oversized`). On
   DBLP-ACM, `__title_key__` produces 1,201 blocks with p99=31, max=104 — safely
   under `max_safe_block` — so `_all_single_oversized` is `False` and compound
   blocking is **never attempted**, regardless of how many total comparisons the
   safe-sized blocks add up to.

   Separately, the RED reason that DOES fire here (`blocking_skewed`, on
   `largest_block_pair_share = C(104,2)/33,563 = 0.16`) has exactly two
   registered rules (`autoconfig_rules.py`: `rule_blocking_too_coarse` `@targets
   ("blocking_skewed","blocking_too_coarse")` at line 306-307, and
   `rule_blocking_adaptive_on_p99_outlier` `@targets("blocking_skewed")` at line
   1727-1728), and **both gate on block SIZE** (`p99 >= 1000`, `p99 > 10*avg`).
   With p99=31 both decline on every iteration. The diagnosis (a SHARE metric)
   and the only two remedies (SIZE metrics) are on different axes, so this
   dataset shape is structurally unanswerable today — confirmed by re-reading
   both functions against current `main`.

## Proposed fix

Two changes, same PR, because the second is inert without the first:

### 1. Widen Gate 2's trigger

In the block around `autoconfig.py:4479-4496`, attempt `_build_compound_blocking`
when EITHER `_all_single_oversized` OR the committed single key is
share-skewed (reuse whatever computes `largest_block_pair_share` for the
`blocking_skewed` RED reason — do not recompute it a second way).

`_build_compound_blocking`'s own selection logic (`autoconfig.py:2660-2726`)
does not need to change: it already evaluates candidate compound pairs by
measured `max_block` (smallest-safest-first, `autoconfig.py:2684-2694`) using
real grouping guards, not labels. Widening *when* it's called is the whole
change; nothing about *how* it picks needs touching.

### 2. A compute-budget guard on the widened path only

The share-skew trigger is new territory: Gate 2 has only ever fired for
oversized keys before, where a compound refinement is unconditionally a
win (it's fixing a correctness problem, not trading anything). Firing it for
a *safe* key on share-skew alone changes the trade calculus, and the issue's
own history has one directly relevant data point: a similarly-shaped
"recall floor" fix was built and measured, and rejected — not because it was
wrong on `dblp_acm`/`person`, but because it cost 22.5x/22.6x more
comparisons and the only gating panel available (`suggest-quality`) is too
small to ever see that cost.

So: accept the compound refinement from the widened trigger only if it does
not increase `total_comparisons` versus the single-key baseline by more than
a fixed multiplier (reuse `_RECALL_TRADEOFF_RATIO` from #2546 if its shape
fits, else a new named constant — **open question, see below**). On
DBLP-ACM this is a non-issue by construction (compound-of-two is a
conjunction, so candidates only ever go down relative to the single key it
extends) — the guard exists for whatever OTHER dataset shape reaches this
new path and doesn't have `year`'s "zero recall cost" property.

## Open design question — needs your call before implementation

The budget guard's shape is the one piece of this I'm not comfortable
picking unilaterally, because it's the exact axis the parked "recall floor"
attempt got wrong, and this issue is already three retracted comments deep
from people (including you) reasoning about this dataset without running it.
Options, roughly in order of how much new machinery they need:

- **(a) No new guard — rely on the conjunction property.** A compound key
  is `col_A AND col_B`; its block for any record is a subset of `col_A`'s
  block. Total comparisons under a compound key are provably `<=` the
  single-key baseline for the SAME `best` column, so if `_build_compound_
  blocking` always compounds onto the key that's already committed (never
  a different column), there is no comparison-count regression to guard
  against, only a recall one — and recall is unaffected by definition (a
  restriction of the block, not a re-derivation of it) *unless* the second
  column has nulls that would drop rows out of blocking entirely, which
  `_component_null_ceiling` already guards.
- **(b) Reuse `_RECALL_TRADEOFF_RATIO`** (#2546) as-is as the budget check,
  even though it was built for a different rule.
- **(c) A new, separate named constant/env knob** scoped to this path only,
  so tuning it can't silently move the parked recall-floor rule too.

I'd lean (a) — it's the smallest change and the conjunction argument holds
structurally for `title + year` and for any compound-onto-committed-key
shape — but I have not proven there is no dataset where it breaks down, and
that proof (or a counter-example) is exactly the kind of thing that needs
`bench-quality-scale`/QIS, not reasoning from one dataset.

## Validation plan

Per the issue's own exit condition: **`bench-quality-scale` / QIS, not the
suggest-quality panel** — every dataset on that panel is small enough that a
comparison-count regression is invisible by construction. This cannot be
validated locally (`feedback_no_local_scale_benchmarks`) or by re-deriving
the DBLP-ACM numbers by hand again — the CI benchmarks lane (#2718 made this
lane able to signal a real change again, per the issue's 2026-08-21 comment)
is the only place this can be measured honestly.

Acceptance criterion (unchanged from the issue body, reproduced independently
three times in the thread): `title_key + year` on DBLP-ACM —
33,563 → 5,749 candidates, recall unchanged at 0.9717, ceiling 0.064 → 0.376
— with **zero** regression on every other dataset in the scale panel.

## Out of scope

- Gate 1 / the surrogate-key guard — already shipped (#2727), reconfirmed
  in force on current `main`.
- The estimator's biased denominator — real, partially mitigated by #2546,
  the issue's own measurement shows it provably reorders nothing here.
- `year` missing from the committed *matchkey* (noted in the 2026-08-22
  comment as "a finding that outgrows this issue") — blocking-only scope.
- Any change to `rule_blocking_too_coarse` / `rule_blocking_adaptive_on_p99_
  outlier`'s existing size-based triggers — they stay as-is; this adds a
  path around them, not a change to them.
