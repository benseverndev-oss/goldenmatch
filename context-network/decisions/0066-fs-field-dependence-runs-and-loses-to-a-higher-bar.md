# 0066 — The FS field-dependence correction runs, and loses to raising the bar

**Status:** proposed (2026-09-10) • **Supersedes:** [0065](0065-fs-field-dependence-cannot-reach-its-target.md) • **Measured:** [34427439165](https://github.com/benseverndev-oss/goldenmatch/actions/runs/34427439165) (c=9), [34429232050](https://github.com/benseverndev-oss/goldenmatch/actions/runs/34429232050) (c=3), [34430101626](https://github.com/benseverndev-oss/goldenmatch/actions/runs/34430101626) (c=5), [34405408541](https://github.com/benseverndev-oss/goldenmatch/actions/runs/34405408541) (reach probe) • **Code:** #2918, #2919, shipped default-OFF

## Context
ADR 0065 concluded the correction "cannot reach the effect it was built for",
because on person data the blocking key is the correlated pair and the
estimator excluded blocking-conditioned fields, so zero pairs were evaluated.
#2918 removed that exclusion. The correction then provably fired — up to
**−7.62 bits** (`street × dob`) — and the panel still read `dF1 +0.0000` on all
five datasets.

That flat line was not a finding about the correction. It hid **three stacked
defects**, each of which alone kept the correction out of scoring:

1. **Blocking-conditioned pairs excluded from the estimate** (0065's finding;
   fixed in #2918).
2. **`_combine_em_sessions` discarded it.** Multi-pass blocking — the default —
   rebuilds a fresh `EMResult` by enumerating fields by hand. `tf_freqs` was on
   the list because forgetting it once made a model "look complete";
   `joint_corrections`, added later, never was. The same constructor dropped
   `calibrated_link_threshold`, so `GOLDENMATCH_FS_CALIBRATE_THRESHOLD` was
   inert on every multi-pass run while logging a calibrated value.
3. **Three of four native scoring routes ignored it.** `_fs_native_eligible(mk)`
   sees only the matchkey, and the native kernel returns pre-normalized scores,
   so any Python post-adjustment is lost when it runs. Only
   `probabilistic_block_scorer` declined. The bucket kernel (the default route),
   the bucket-batch path and the out-of-core path did not, and
   `score_probabilistic_blocks_batched` used the check with inverted polarity —
   that fourth site was found by a guard test, not by hand.

After all three were fixed the partition changed on four of five datasets.

## Decision
**Keep the correction default-OFF. With each arm allowed its own threshold it
does not beat the baseline on any panel dataset.**

A single-bar A/B cannot judge this lever, in either direction. The correction's
purpose is to remove evidence FS invented by double-counting, so it shrinks the
total evidence scale; a bar held fixed across both arms measures the scale
change, not the correction. At c=9 it read **−0.7630** on cotenant (a bar too
high for the corrected scale); at c=3 it read **+0.0342** (a bar too low for the
uncorrected one). Both were artifacts. The fair comparison is best-achievable
per arm:

| dataset | OFF best F1 (c) | ON best F1 (c) | verdict |
|---|---|---|---|
| person | 1.0000 (3, 5, 9) | 1.0000 (3, 5) | tie |
| ncvr_synthetic | 0.9986 (9) | 0.9986 (9) | tie — partition identical at every bar |
| historical_50k | **0.7843** (9) | 0.7656 (9) | OFF |
| household_hardneg | **1.0000** (3, 9) | 0.9775 (3, 5) | OFF |
| cotenant_hardneg | **0.9959** (9) | 0.9067 (3) | OFF |

OFF also dominates the precision–recall frontier, not only the summary metric:
on cotenant, OFF at c=5 reaches **P 0.872 at R 1.000**; ON's best point is
**P 0.868 at R 0.950**. No operating point exists where the correction buys
precision that raising the bar does not buy more cheaply.

The mechanism is plausible rather than broken. On cotenant `street × dob`
co-agrees at ~197× independence among non-matches, so a genuine duplicate's
honest joint evidence for that pair is ~+1 bit, not the ~+8.5 bits naive FS
awards. The correction computes the right number. What it does not do is
separate true from false merges any better than the uncorrected weight already
does once the cut is placed well.

## Limits of this verdict
- Bars measured: c ∈ {3, 5, 9}. c=1 was attempted and abandoned — the baseline
  linked 99.0% of records and exhausted the cluster-split budget, a degenerate
  point. ON's cotenant optimum could sit between 1 and 3; its recall is already
  0.950 at c=3, so the room is narrow.
- Zero-config panel configs only. A shape where correlated fields drive
  over-merge *and* no clean high bar exists is not represented.
- The targeting rule — subtract on top-level co-agreement of both fields — is
  the only rule measured. A rule that applies only where other evidence is weak
  is a different design and is not ruled out by this.

## Consequence
- **0065 is superseded.** Its immediate observation (zero pairs evaluated) was
  correct; its conclusion was not, and it named one of three reasons.
- **The durable output is the instrumentation, not the lever.**
  - `ab_lever` reports predicted-pair count and a partition digest per arm, so
    "flat F1" now says whether the lever was inert or absorbed. Its self-test
    asserts a split cluster changes the digest and runs before every
    measurement. Its first real use caught a void run on a branch that predated
    the TF fix.
  - `_fs_native_route_eligible(mk, em_result)` is the single native routing
    decision, and a guard test fails any direct call to the matchkey-only check.
  - A guard test fails any `EMResult` field that `_combine_em_sessions` drops
    without declaring it recomputed.
  - `GOLDENMATCH_FS_FD_PROBE` reports how many touched pairs sit in the
    flippable band `[cut, cut+bits)`.
- **Two previously rejected weight-space levers were re-measured on the
  evidence-cut axis and fail harder there**: post-blocking-u (historical_50k
  −0.3596) and TF adjustment (−0.2240, with predicted pairs nearly doubling).
  Neither was rescued at a single bar.
- **TF addendum (2026-09-10): swept further, the verdict splits by dataset and
  TF still stays off.** Under the default calibration, lowering the TF clamp
  (`GOLDENMATCH_FS_TF_CLAMP`) moves historical_50k from −0.0116 (clamp 10) to
  −0.0043 (6) to **+0.0058** (3), so bump size drives that regression. But
  cotenant (−0.0178) and household (−0.0041) are identical at every clamp, down
  to the pair count: none of their adjustments exceeds 3 bits, so their
  regression has another cause. On the evidence-cut axis TF's best bar beats the
  baseline's best on historical_50k (0.8482 vs 0.8186, both c=12) and loses on
  cotenant (0.9851 vs 0.9959). **No single evidence cut replaces today's
  default**: swept over c ∈ {3, 5, 9, 12, 15}, the baseline's historical_50k peaks
  at 0.8186 (c=12) against the default's 0.8320, and household and cotenant
  are already falling by then.
- **Native and numpy TF scoring disagree.** Same commit, clamp and data, only
  the route differs: historical_50k 303,998 vs 303,314 predicted pairs (F1 0.8195
  vs 0.8204), cotenant 1,287 vs 1,279. The one CI lane running the TF
  route-parity tests deselects the native ones. Tracked in #2922.

## Addendum: the match-class term (2026-09-10)
**Measured:** net correction [34491766633](https://github.com/benseverndev-oss/goldenmatch/actions/runs/34491766633) (c=3), [34491769702](https://github.com/benseverndev-oss/goldenmatch/actions/runs/34491769702) (c=5), [34491773407](https://github.com/benseverndev-oss/goldenmatch/actions/runs/34491773407) (c=9), [34491777072](https://github.com/benseverndev-oss/goldenmatch/actions/runs/34491777072) (c=12); non-match-only rerun [34491840682](https://github.com/benseverndev-oss/goldenmatch/actions/runs/34491840682) (c=3), [34491844272](https://github.com/benseverndev-oss/goldenmatch/actions/runs/34491844272) (c=5), [34491847161](https://github.com/benseverndev-oss/goldenmatch/actions/runs/34491847161) (c=9), [34491780279](https://github.com/benseverndev-oss/goldenmatch/actions/runs/34491780279) (c=12). All on one commit; every paired baseline arm identical.

A literature review found the original correction misspecified in a testable way.
Loglinear record-linkage models put field interactions in the match class, the
non-match class, or both (Daggy et al. 2013 selected both by BIC), and Xu et al.
2019 found dependence modelling matters when it is present in the DOMINATING
class. The original subtracted only the non-match lift, and inside
historical_50k's blocks match prevalence is ~0.92.

The correction now subtracts the net double-count,
`log2(u_ab/(u_a·u_b)) − log2(m_ab/(m_a·m_b))`, by default. Measured on `main`
after #2924 and #2926 fixed the numpy route the correction scores on, each arm at
its own best bar over c ∈ {3, 5, 9, 12}:

| dataset | baseline | non-match only | net (both classes) |
|---|---|---|---|
| historical_50k | **0.8186** (c=12) | 0.7812 (c=12) | 0.8124 (c=12) |
| cotenant_hardneg | **0.9959** (c=9) | 0.9083 (c=3) | 0.9083 (c=3) |
| household_hardneg | **1.0000** (c=9) | 0.9775 (c=5) | 0.9775 (c=5) |
| ncvr_synthetic | 0.9986 | 0.9986 | 0.9986 |
| person | 1.0000 | 1.0000 | 1.0000 |

- **The match-class term is real where it exists.** historical_50k's
  `surname × postcode_fake` carries +2.33 b of match lift against +3.69 b
  non-match. The net correction cuts historical_50k's loss at the same bar from
  −0.0374 to −0.0062 (c=12) and from −0.0049 to −0.0002 (c=3).
- **Elsewhere it is about zero** (`street × dob` +7.62 vs +0.00,
  `last_name × email` +5.42 vs +0.00). True duplicates agree on those fields
  almost always, so their joint cannot exceed independence by much, and the net
  stays at the full non-match lift.
- **The net correction is at least as good as the original at every bar on every
  dataset, so it is now the default**; `GOLDENMATCH_FS_FD_CLASSES=nonmatch`
  restores the original. **It still beats the baseline nowhere**, so the lever
  stays default-OFF and this ADR's verdict stands on the fixed numpy route. The
  original's best numbers barely moved (cotenant 0.9067 → 0.9083).
- **Correction to the Decision table above:** its historical_50k baseline best,
  0.7843, came from c ∈ {3, 5, 9}. With c=12 it is 0.8186.
- **Fitting the interactions inside EM is not pursued.** Where the dependence is
  two-class, the net correction is already near neutral; where it is not, a
  proper fit estimates the same near-zero match interaction. This matches Xu et
  al. 2019's "comparable matching accuracy", and Domingos & Pazzani 1997's result
  that a naive-Bayes classifier's decisions can survive violated independence
  while its probability estimates do not.

## Method note
Every panel verdict on this lever between #2914 and #2919 was reasoning about a
measurement of code that did not execute. The July 2026 spike is the exception:
it predated the default multi-pass bucket route, did reach scoring, and found a
precision-for-recall trade at every threshold it tried (historical_50k P +0.0167,
R −0.0279). That agrees with this verdict — two independent measurements, seven
weeks apart, on different code paths. Each fix that made it more real made
the result look worse, and twice the sign of a headline number was an artifact
of where the bar sat. Two rules came out of it: a flat metric is not evidence of
neutrality until the partition is shown to have changed or not, and a bar that
can unfairly punish one arm can unfairly flatter it — check both directions.
