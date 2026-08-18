# Incident: zero-config lost half its matches, silently

**Status:** fixed, verified, merged (#2671). person@100K pairwise
**F1 0.554 -> 0.9970**, recall **0.383 -> 0.9949**, precision **0.9992** --
matching the expert-configured `gm_probabilistic_shipped` lane exactly
(120,269 pairs). All four defects below are on `main`.

**There is an addendum at the bottom** covering a FIFTH instance of the same
pattern one layer down (#2673), found while closing these out. It took three
designs; the quality gate rejected the first two. Read it before touching
`ScoringProfile` or `pick_committed`.

**Severity:** high. Zero-config is the path a user with no ER expertise takes,
and it was the worst-performing of the three lanes. Every unit test was green
throughout.

---

## What was wrong

Four defects. Each is individually small; together they halved recall on the
default path and then capped precision.

**1. The controller never measured its blocking profile.**
`_should_measure_blocking` gated full-frame measurement on
`strategy == "static"`, and zero-config commits `multi_pass` (the #1207
per-identifier union). So it always reasoned over a profile extrapolated from a
~6K sample, which arrived as **all zeros**.

**2. A policy rule fired on that zero and deleted the blocking plan.**
`reduction_ratio` defaults to `0.0`, so `0.0 < 0.5` was true at iteration 0 --
before any pipeline run existed -- and `rule_low_reduction_ratio` "repaired" a
ratio nobody had measured. The real value for that plan is **0.9757**: it should
never have fired. Firing, it rebuilt `passes` from `keys`; for a multi_pass plan
`keys` holds only the primary key, so **six of eight passes were discarded**.

**3. Fixing (1) exposed a latent routing defect.**
Once the planner saw a *measured* ~121M candidate pairs instead of a tiny
extrapolated count, `rule_chunked` fired at its >= 50M threshold and set
`backend='chunked'`. `_fs_use_bucket_route` excluded `chunked`, so FS fell to the
legacy batched scorer, which retained **9,250** pairs where bucket retained
**120,269** on identical candidates.

**4. The resolved cutoff was stamped from a FALLBACK, pinning it.**
`_stamp_resolved_link_thresholds` (#2637) records the sample-run's FS cutoff on
the committed config so the value is explicit and tunable, and states: *"This
does NOT change the cutoff ... behaviour is byte-identical."* True for a
`calibrated` value; false for a `fallback`. Once `link_threshold` is set it
counts as `configured`, so the full-data run **skips the threshold refit**
(`refit: {"reason": "explicit-link-threshold"}`) and is pinned to a number
derived from a ~6K sample -- while `fallback` means precisely that nothing
decided it. Measured at person@10K, identical in every other respect:

    stamped fallback 0.50    P 0.9947  R 1.0000  F1 0.9973
    left None (refit runs)   P 1.0000  R 0.9979  F1 0.9990   <- == probabilistic lane

At 100K the same pin cost precision **0.9308** against the probabilistic lane's
**0.9992** -- over-merge, with recall already at 0.9995.

## The pattern: a non-decision laundered into a decision

Three of the four defects are the same mistake wearing different clothes. In
each, a value that meant *"nothing decided this"* was consumed downstream as if
it were evidence:

| Where | The non-decision | Read downstream as |
|---|---|---|
| Blocking profile | never measured (sample-extrapolated) | measured zeros |
| `reduction_ratio` | field default `0.0` | "the ratio is 0.0, repair it" |
| `link_threshold` | `source: fallback` (a fixed default) | `configured` -> skip the refit |

The engine had no way to distinguish **absent** from **zero**, or **defaulted**
from **chosen**. That is the design lesson worth more than any individual fix:
a measurement pipeline needs "not measured" to be representable and to
propagate, otherwise defaults silently acquire the authority of observations.

The repaired `rule_low_reduction_ratio` now declines on `n_blocks == 0`
explicitly, and the stamp only records `calibrated`. Both are the same
correction: refuse to act on a non-decision.

## Why nobody noticed

Defects 1-3 lose **candidates** rather than mis-scoring them. A dropped pass, an
unmeasured profile, a scorer that never sees a pair -- none can invent a false
match. **Precision stayed at 1.0000 while recall collapsed.** There is no
alarming signal in that shape; it looks like a clean run. (Defect 4 is the
mirror image: recall 0.9995 with precision sagging.)

The one gate that would have caught it did not exist: nothing asserted on the
answer zero-config produces.

## Fixes

| # | Fix |
|---|-----|
| 1 | `_should_measure_blocking` covers `multi_pass`; the refusal path measures before refusing |
| 2 | `rule_low_reduction_ratio` declines on an unmeasured profile (`n_blocks == 0`) and preserves `passes` |
| 3 | `chunked` joins `duckdb`/`polars-direct` on the FS bucket route -- a memory strategy is not a scoring decision |
| 4 | Only a `calibrated` cutoff is stamped; a `fallback` is left `None` so the full-data refit runs |

Shipped separately and independently valuable:

* **multi_pass full-frame measurement, vectorized per pass** -- 15,953 ms ->
  1,094 ms (14.6x), byte-identical profile (#2667)
* **blocking pair budget** -- 121,391,850 -> 50,054,456 comparisons (**-59%**) at
  unchanged F1 0.9970, by bounding diversified passes on pairs/row rather than
  block rows (#2670)
* **`_projected_max_block` arrow repair** -- it built polars expressions that
  RAISE on a `pyarrow.Table` and landed in `except: return 0`, where 0 reads as
  "safe". The #1857 scale guard had been silently disabled on the default arrow
  lane (#2670)
* **bench telemetry** -- reported `blocking.keys` (the primary key) as the whole
  plan, so an 8-pass plan printed as one pass (#2669)

## The other lesson: four instruments lied

Each cost a wrong conclusion, and together they are why this took as long as it
did.

| Instrument | The lie |
|---|---|
| Refit decision | Commit path logged at INFO, all three DECLINE paths at DEBUG -- the interesting case was silent |
| `_projected_max_block` | Raised on arrow, caught by `except: return 0`, and 0 means "safe" |
| `_config_telemetry` | Read `.keys` before `.passes`; an 8-pass plan reported as 1 |
| `block_count` | `block_count_scored or block_count` -- two quantities behind one name, making "8 passes produced fewer blocks than 2" look real |

## Method post-mortem

Eight hypotheses were tested and discarded: blocking plan, link threshold,
`_skip_finalize`, `rule_low_reduction_ratio` (dismissed, then correct after all),
backend/bucket routing, "the PR contains a canceller", "the two halves are
separable", emitter leak. Most cost a ~20 minute CI round.

Every actual answer came from an instrument or a mechanical diff, never from a
hypothesis:

* dumping **all 26** config fields named `backend` immediately, after three
  rounds of guessing one field at a time;
* the **replay lane** (zero-config's own config through the explicit path) proved
  config-not-path and collapsed the loop from 20 minutes to **5 seconds**; four
  hypotheses died in the next two minutes;
* the **per-pass blocking trace** proved blocking was healthy and identical in
  both lanes, eliminating the entire blocking class at once -- and its first
  version came back EMPTY, revealing that `_build_multi_pass_blocks` is not even
  on the bucket path;
* defect 4 was then found in a **single 2-minute local bisect** on the fast loop,
  where the same question had previously cost a CI round.

**What should have happened:** with a working lane and a broken lane on the same
fixture, go straight to *reproduce smallest and fastest -> diff exhaustively ->
bisect mechanically -> explain last*. The differential was available from the
first hour.

**Rules taken forward**

1. Build the instrument before forming the theory.
2. Verify a number means what its name says before building on it.
3. Find the fastest reproduction first; a 5-second loop is worth an hour of setup.
4. A gate nobody has watched fail is a gate that measures nothing.
5. Keep "not measured" distinguishable from "measured zero", and "defaulted"
   from "chosen" -- three of these four defects are that distinction collapsing.

## Guard rails added

* `test_no_policy_rule_shrinks_the_blocking_plan` -- no policy rule may return
  fewer blocking passes than it was given. **Verified by restoring the bug:**
  `rule_low_reduction_ratio returned 2 blocking passes from 4`. Covers future
  rules, not just this one.
* `test_stamp_only_calibrated_threshold` -- a `fallback` cutoff is never
  stamped; a `calibrated` one still is, so #2637's lever survives.
* `test_bucket_equals_legacy_probabilistic` -- the parity matrix cited as
  evidence that bucket and legacy agree covered `weighted` 3x and
  `probabilistic` 0x. Zero-config resolves to probabilistic.
* `test_fs_route_chunked_keeps_bucket` -- pins the FS backend allow-list in both
  directions, including that ray/datafusion keep their own routing.
* `test_zeroconfig_does_not_collapse_on_person_data` -- end-to-end floor.
  **Honest limitation:** it does NOT catch this bug (at ~1,100 rows the rule
  never fires); it guards the over-merge class instead. An early version of its
  own fixture used ten first names and five cities, produced P 0.0461 /
  R 1.0000, and had to be rebuilt with realistic cardinality -- which is exactly
  the failure it now watches for.
* Per-pass blocking decision trace, kept permanently.

## Open

* ~~100K confirmation of defect 4~~ **CONFIRMED.** Precision moved 0.9308 ->
  0.9992 at 100K, matching the probabilistic lane. No second cause.
* The legacy batched FS scorer genuinely diverges from bucket at scale on
  probabilistic matchkeys. Routing now avoids it; the divergence itself is
  unexplained, and small-fixture parity passes.
* **#2668 is NOT closed by any of this.** Its stated mechanism (`health()`
  could never return YELLOW) is real and fixed in the addendum's work, but
  `goldengraph-pipeline` -- the lane it actually reports -- fails IDENTICALLY
  with those fixes applied (dispatched on the branch to check, rather than
  inferred). Its over-merge has another cause. The lane went green->red between
  2026-08-17 and 2026-08-18; #2648 is in that window per the issue's own bisect,
  but so are #2655 ("the data-driven cut admitted TWICE the match rate EM
  estimated"), #2650, #2646, #2645 and #2644, and bucket's emitted profile has
  changed again since. That bisect wants re-running against the current tree.

---

# Addendum: the same pattern, one layer down (#2673)

The four defects above were all "a non-decision read as evidence". Closing them
surfaced a fifth instance in the layer underneath, and it took three designs to
fix because the first two were rejected by the quality gate.

## The defect

`_emit_scoring_profile` is handed pairs that have ALREADY cleared the cut --
its own docstring says so -- and computed `mass_above(scores, threshold)` over
exactly those. That is **1.0 by construction** for any non-empty result, at all
six emit sites, for every matchkey type. The field carried one bit ("did
anything match") while about a dozen consumers read it as a fraction.

Verified before touching anything, on two unrelated datasets:

    orgs_hard, WEIGHTED mk, real threshold=0.8    3 pairs, 0 below, mass 1.0000
    healthy high-recall synthetic                8,343 pairs, 0 below, mass 1.0000

The first is a weighted matchkey with a genuine configured cutoff, so this is
NOT the probabilistic `min(scores)` fallback #2668 blamed. That fallback makes
it worse; it is not the cause.

## Two consumers, two user-visible bugs

| consumer | what it asks | what it got |
|---|---|---|
| `pick_committed` precision-collapse guard | "did EVERYTHING match?" | 1.0 always -> demoted every RED entry that matched anything -> v0 (which matched nothing) won -> **confident empty result** (#2663) |
| `health()` YELLOW branch | "is borderline mass swamping the tail?" | needs `mass_in_borderline > 1.0` -> **unreachable**, so any matching run was GREEN or RED with no "looks marginal" (#2668) |

## Three designs; the gate rejected two

**1. Rebase `mass_above_threshold` itself.** Correct about the field, wrong
about blast radius. The rules gate on hardcoded cuts (`< 0.5` in
`rule_blocking_too_coarse`, `>= 0.95` in the precision anchor, `>= 1.0` in
`rule_recall_gap_suspected`) every one of which was chosen while that input was
a CONSTANT. Gate: `anchor_person_match` **F1 1.0000 -> 0.7303** (P -> 0.5751),
the controller taking a different rule path and over-merging.

**2. Also migrate zero-label's everything-matches guard.** That guard turned
out to be the ACTUAL deciding consumer on `orgs_hard` --
`pick_committed(collapse_floor)` picks iteration 3 (463 pairs) while the same
call with `use_zero_label_confidence=True` (on by default) picks v0 (0 pairs).
Migrating it fixed `orgs_hard` and cost the anchor **F1 1.0000 -> 0.5139**: its
tautological cap was the only thing penalising over-merge there.

**3. What shipped.** The honest fraction as a NEW field,
`ScoringProfile.admitted_fraction`, read by exactly two consumers and only
where it is not None. Everything else stays on the signal it was calibrated
against. Plus the mirror of the collapse guard: **DEGENERATE-EMPTY** -- an
entry that merged nothing must not beat one that merged something.

## The measurement that picked the third design

Design 3's over-merge signal was chosen by ruling out the obvious one. No
cluster metric separates `orgs_hard`'s CORRECT entry from the anchor's
OVER-MERGED one:

| signal | orgs_hard it3 (good) | anchor it1 (over-merged) |
|---|---|---|
| `cluster_size_max / n_rows` | 0.0154 | 0.0156 |
| `oversized_cluster_count` | 0 | 0 |
| `measured_bridge_risk` | 0.0 | 0.0 |
| `transitivity_rate` | 0.005 | 0.085 |

"Did it merge anything at all" separates them cleanly, and is the pathology
rather than a proxy for it:

    orgs_hard   n_rows 845, v0 -> 845 clusters (0 merges)    BAD
    anchor      n_rows 706, v0 -> 400 clusters (306 merges)  GOOD

Result: `orgs_hard` F1 0.0000 -> 0.4108, `anchor_person_match` unchanged at
1.0000.

## Rules taken forward, added to the five above

6. **A field that is constant is not a signal, and the rules calibrated against
   it are load-bearing on the constant.** Fixing the field breaks them all at
   once. Ship the honest value BESIDE the old one and migrate consumers with
   measurement, one at a time.
7. **Before proposing a replacement signal, check it actually discriminates.**
   Design 3's first draft named `ClusterProfile` as the over-merge signal; a
   five-minute probe showed the good and bad cases are indistinguishable on
   every one of its metrics. That probe was cheaper than the design would have
   been.
8. **A guard that looks broken may be load-bearing by accident.** Zero-label's
   everything-matches cap is tautological AND the only over-merge protection on
   one gate dataset. "This is wrong" does not imply "removing it is safe".
9. **Local gate numbers are invalid without the CI env.** `anchor_person_match`
   read 0.9384 locally and 0.7303 in CI until `GOLDENMATCH_AUTOCONFIG_MEMORY=0`
   was matched. An A/B against the branch's own base in a FIXED env is valid
   even when the absolute numbers are not.

## The instrument lied again, and this time in the shell

Two GitHub issues (#2674, #2675) were filed claiming fixes were missing from
`main`. Both were wrong. The check was:

    git cat-file -e origin/main:$f 2>/dev/null && echo ON MAIN || echo MISSING

On Git Bash, `rev:path` is path-mangled, so `git` errors for EVERY file,
`2>/dev/null` hides it, and the `else` branch reports MISSING uniformly. Same
absent-vs-zero collapse as the four defects above -- a failed command's empty
output scoring as a measured zero -- just in a shell pipeline instead of the
engine. Both issues closed as invalid.

Rule 10: **a `grep -c` of 0, or a uniform "missing", is a claim about the
pipeline before it is a claim about the repo.** Check the upstream command ran.
