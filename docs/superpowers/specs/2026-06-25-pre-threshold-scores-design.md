# Pre-Threshold Scores for the Suggestion Kernel — Design

**Date:** 2026-06-25
**Status:** Approved (brainstorm); pending plan
**Author:** Ben Severn (with Claude)
**Follows:** `2026-06-24-config-suggestion-kernel-design.md` (Plan 1),
`2026-06-24-suggester-gym-design.md` (the gym), `2026-06-24-health-proxy-redesign-design.md`
(the proxy redesign that hit the same root cause)

## Problem

The gym exposed Finding #2: the config-suggestion kernel emits `raise_threshold`
for **every** threshold/scorer perturbation (`threshold_too_high`,
`bad_freetext_scorer` go negative in raw recovery); `lower_threshold` and
`swap_scorer` never fire on degraded configs.

### Root cause (confirmed with instrumentation)

The adapter (`review_config`) builds the kernel's `scored_pairs` Arrow batch from
`EngineResult.scored_pairs`, which is **threshold-filtered** — `_run_pipeline`
emits only pairs `>= threshold` (health.py's own docstring flags this). So the
kernel is **blind to the entire score distribution below the threshold.**
Instrumented on `ncvr_synthetic` (all three configs):

| config | threshold | min score | frac_below_thr | mass_above | mass_just_below | emitted |
|---|---|---|---|---|---|---|
| ceiling | 0.80 | 0.800 | 0.000 | 1.000 | 0.000 | raise ×2 |
| threshold_too_high | 0.90 | 0.900 | 0.000 | 1.000 | 0.000 | raise ×2 |
| threshold_too_low | 0.65 | 0.650 | 0.000 | 1.000 | 0.000 | raise ×2 |

The min score *always equals the threshold*; `frac_below_thr` is *always 0*.
Consequences in the kernel's threshold rule:
- `mass_above` is **always 1.0** → the "everything matches → `raise_threshold`"
  path fires on every config.
- `mass_just_below` is **always 0** → the recall-risk `lower_threshold` path can
  **never** fire.
- The dip is found near the top of the truncated [threshold, 1.0] band (~0.96) →
  spuriously suggests raising.

So `raise_threshold` is the only threshold suggestion that can ever be emitted —
not a rule-logic bug, but **the kernel being fed a truncated distribution.**

### The unifying insight

This is the SAME root cause behind the proxy work's failures. Every precision
signal we tried (cohesion's `avg_conf`, the pseudo-label specificity) came out
**saturated** for the same reason: the score distribution the suggester sees has
no left tail. One artifact — threshold-filtered scores — breaks `lower_threshold`,
spuriously fires `raise_threshold`, distorts the dip, **and** saturates the proxy
precision side. Fixing it is the most leveraged change available.

### Scope

In scope: feed the kernel the full (pre-threshold) candidate-pair score
distribution, gated, proven on the gym. The kernel and its rules are UNCHANGED —
they compute correctly given correct input.

Out of scope: the default-on flip (separate evidence-backed change); the
histogram-only scale optimization (parked); the `swap_scorer` corruption-signal
threshold (if the gym shows `swap_scorer` still under-fires after this, that's a
follow-up — this fix targets the threshold-distribution blindness).

## Approach

Three were considered:

- **A — Adapter diagnostic re-score at threshold ≈ 0 (chosen).** `review_config`
  builds `scored_pairs` from a separate run with the matchkey threshold forced to
  ~0, so the batch carries the full candidate distribution. Adapter-only; kernel
  and pipeline unchanged. Proven viable (lowering threshold to 0.65 surfaced 7565
  pairs vs 2442).
- **B — Reuse the controller's `ComplexityProfile` score histogram.** No extra
  run, but the profile only exists for the zero-config baseline computed during
  `auto_configure` — absent for the perturbed/candidate configs `review_config`
  evaluates. Too limited.
- **C — Pipeline emits a candidate-score histogram alongside the filtered
  result.** Single run, scale-safe (histogram not pairs), but touches the
  pipeline/`EngineResult` — broader blast radius. The eventual scale
  optimization, not the minimal first fix.

**Decision: A.** Minimal, adapter-only, kernel/pipeline untouched, empirically
grounded. Optimize to C later if cost matters. B is a dead end for candidates.

## Architecture

All changes in `review_config` (`packages/python/goldenmatch/goldenmatch/core/suggest/adapter.py`):

- **Real run → `clusters`** (cluster diagnostics) — UNCHANGED.
- **NEW diagnostic run → `scored_pairs`:** deep-copy the config, force every
  matchkey threshold to `0.0`, run it, build the `scored_pairs` Arrow batch from
  THAT run — now the full candidate-pair score distribution (the sub-threshold
  tail the kernel was blind to).
- **`column_signals`** — UNCHANGED.
- Call `suggest()` exactly as before. The kernel's
  `ScoreDiagnostics::from_batch(scored_pairs, threshold, bins)` is passed the REAL
  threshold via `ConfigSummary`, so with a full `scored_pairs` it computes real
  `mass_above` (< 1.0), real `mass_just_below` (so `lower_threshold` fires), and a
  real bimodal dip. No kernel change.

**Gating (measure-first).** `GOLDENMATCH_SUGGEST_FULL_DIST` ∈ {`0` (default,
current filtered behavior — byte-identical), `1` (full distribution)}. Default
`0` so nothing changes until the gym proves it; the gym sets `1` to A/B
misfiring-vs-fixed; the flip to default-`1` is a separate evidence-backed commit.

## Diagnostic threshold & cost

- **Diagnostic threshold = 0.0** — capture all candidate pairs the blocking
  produced, so the kernel sees the complete bimodal shape (non-match mode, the
  valley, the match mode). A partial floor risks sitting above the data-dependent
  valley and missing the dip; 0.0 is the safe simple choice.
- **Bound the cost — scoring-only, discard clusters.** The diagnostic run scores
  the EXACT same candidate pairs as the real run (blocking identical; only the
  threshold *filter* differs), so the extra *scoring* cost is ~nil beyond the real
  run. The only blowup risk is *clustering* a huge low-threshold pair set (at
  threshold 0 everything could collapse into one component) — and the diagnostic
  run's clusters are NEVER used. So the diagnostic run should SKIP the cluster
  stage (scoring-only) and return only `scored_pairs`, turning "an extra full run"
  into "an extra scoring pass." Planning confirms whether a scoring-only path is
  cleanly reachable; v1 fallback = full run at threshold 0 with clusters discarded
  (acceptable on the opt-in path).
- **Scale note (YAGNI v1).** Full candidate pairs can be large at scale;
  `review_config` is opt-in/interactive on review-sized data, so passing the pairs
  is fine for v1. The kernel histograms them anyway, so the scale optimization is
  to pass a histogram (the Approach-C single-run-histogram path) — parked.

## Validation

The gym is the scoreboard (`GOLDENMATCH_SUGGEST_FULL_DIST=1`):

**Direct rule-misfire checks:**
1. `lower_threshold` now fires on `threshold_too_high` (real `mass_just_below>0`)
   instead of the spurious `raise_threshold`.
2. Spurious `raise_threshold` stops on the already-good ceiling config
   (`mass_above` no longer 1.0).
3. Raw recovery goes positive on the perturbations that were negative (−250%,
   −834%); gym `score (raw)` climbs out of the −225% hole.

**Dual gate:** gym live recovery rises across more perturbations; oracle
`suggester_precision` holds ~1.0 (suggester now emits correct fixes, fewer harmful
ones reach the gate).

**Unifying bonus check:** re-run the cohesion-proxy gym sweep with `FULL_DIST=1`.
If the de-saturated `mass_above` rescues the precision side, a suggester emitting
*correct* suggestions may thread the dual gate even under a weak proxy — this one
fix could close out the whole arc. Measured, not promised.

Recorded in a findings note (gym table: filtered vs FULL_DIST, per perturbation —
rule fired, recovery, precision).

## Testing

- **Unit (adapter, no kernel needed):** with `FULL_DIST=1`, the `scored_pairs`
  batch the adapter builds contains pairs scoring BELOW the matchkey threshold
  (the sub-threshold tail); with `FULL_DIST=0` it does not — pins the mechanism.
- **No-change guard:** `FULL_DIST=0` (default) → `review_config` output
  byte-identical to current (same `scored_pairs` batch).
- **Behavioral (native-guarded):** on a deliberately too-high-threshold config,
  `review_config(FULL_DIST=1)` emits a `lower_threshold` suggestion (was
  `raise_threshold`) — the misfire fixed, observably.

## Done criteria

- `review_config` sources `scored_pairs` from a threshold-0 diagnostic
  (scoring-only) run when `GOLDENMATCH_SUGGEST_FULL_DIST=1`; `=0` (default) is
  byte-identical to current.
- Unit test pins the sub-threshold tail present/absent by flag; no-change guard
  green.
- Gym run records: `lower_threshold` fires on `threshold_too_high`, spurious
  `raise_threshold` stops on the good config, raw recovery climbs, oracle
  precision held — plus the bonus cohesion re-sweep result.
- Kernel, rules, and pipeline UNCHANGED. Default behavior unchanged.

## Open questions for planning

- Confirm how `review_config` currently runs the engine and obtains
  `EngineResult.scored_pairs` (the `MatchEngine.from_dataframe._run_pipeline`
  path), and whether a scoring-only variant (no cluster stage) is cleanly
  reachable for the diagnostic run — or whether v1 runs the full pipeline at
  threshold 0 and discards clusters.
- Confirm forcing all matchkey thresholds to 0.0 on the deep-copied config does
  not trip a Pydantic validator or change blocking (it must change ONLY the
  score-emit filter, not the candidate set).
- Confirm the kernel receives the REAL (un-forced) threshold via `ConfigSummary`
  for `mass_above`/`mass_just_below` — i.e. the diagnostic threshold is used ONLY
  to widen `scored_pairs`, while the kernel still evaluates against the config's
  true threshold.
- The diagnostic re-score must reuse the SAME `engine` instance / blocking as the
  real run so blocking is provably identical between the two runs — the cost
  argument ("only the score filter differs, blocking is identical") depends on it.
  Pin this in the plan.
- The verify pass currently uses `suggestion_health_from_clusters` *because* the
  scored-pairs proxy was useless under the threshold filter. Once `FULL_DIST=1`
  de-saturates the distribution, the plan should make an explicit decision on
  whether the verify-pass health proxy stays cluster-based or can now also consume
  the full scored-pairs distribution — so the two code paths don't silently
  diverge on which `scored_pairs` they assume.

## Findings (full-dist, 2026-06-25)

Ran the gym + oracle harness on `synthetic` + `ncvr_synthetic`
(`native=0.1.5`, `sha=a2a913df2379`, `--row-cap 20000`, determinism pinned:
`GOLDENMATCH_AUTOCONFIG_MEMORY=0`, `PYTHONHASHSEED=0`). Three variants:
default (flag off), `GOLDENMATCH_SUGGEST_FULL_DIST=1`, and full-dist +
`GOLDENMATCH_SUGGEST_HEALTH=cohesion GOLDENMATCH_SUGGEST_COHESION=min_edge`.

| variant | `threshold_too_high` rule fired | raw gym recovery headline | live gym recovery headline | oracle suggester_precision (synthetic / ncvr) |
|---|---|---|---|---|
| off (default) | `raise_threshold` (the misfire; `lower_threshold` no) | -225.9% | 0.0% | 1.00 / 1.00 |
| full-dist | `lower_threshold` **yes** | -399.3% | -309.2% | 1.00 / **0.00** |
| full-dist + cohesion | `lower_threshold` (raw yes; live gate blocks it) | -399.3% | 0.0% | 1.00 / 1.00 |

Supporting raw numbers (per-perturbation `rec%_RAW`, ncvr_synthetic):

| perturbation | off | full-dist | full-dist + cohesion |
|---|---|---|---|
| `threshold_too_high` (rule `lower_threshold`) | -250.4% (fired `raise`) | **-1231.6%** (fired `lower`) | -1231.6% raw / live blocked |
| `threshold_too_low` (rule `raise_threshold`) | +72.3% | -5.2% | -5.2% |
| `bad_freetext_scorer` (rule `swap_scorer`) | -834.5% | 0.0% (no fire) | 0.0% (no fire) |

### Verdict — honest read

**1. Is the rule-misfire RESOLVED?** *Partially — the rule SELECTION is fixed,
but the resulting fix is harmful.* With `FULL_DIST=1`, `threshold_too_high` now
fires `lower_threshold` (was the spurious `raise_threshold`), confirming the
mechanism the design predicted: a full pre-threshold distribution de-saturates
`mass_above` and surfaces `mass_just_below>0`, so the recall-risk path can fire.
**But the fix it emits is wrong-signed in impact** — recovery on
`threshold_too_high` goes from -250.4% to **-1231.6%**, and the raw gym headline
*fell* from -225.9% to -399.3% (the design hoped it would climb out of the hole;
it dug deeper). So the kernel now picks the *right rule name* and applies a
*destructive parameterization*: lowering the threshold on the ncvr config floods
in false-positive pairs (degraded F1 0.9102 → far worse). The design's premise
("the kernel computes correctly given correct input") does not hold for the
`lower_threshold` *magnitude* on this dataset — feeding the full distribution
fixed the dispatch but exposed a second bug in how far the rule lowers the
threshold. `swap_scorer` and `raise_threshold` did NOT improve either.

**2. Did oracle precision hold (~1.0, no net-negative)?** *No — full-dist alone
regresses precision.* `suggester_precision` on ncvr_synthetic dropped from 1.00
to **0.00**: the one suggestion full-dist emits on ncvr is net-negative against
ground-truth F1. synthetic held at 1.00 (it emits no suggestion). So full-dist
on its own violates the no-net-negative bar the oracle exists to protect.

**3. Did the cohesion bonus check rescue the health proxy?** *It prevents the
harm, but does not deliver a win — it rescues precision by suppression, not by
threading the gate.* With full-dist + cohesion (`min_edge`), the live gate
catches the harmful `lower_threshold` (`expected_rule_fired_live=no` on
`threshold_too_high`) and oracle precision is restored to **1.00 / 1.00**.
That is the design's "unifying bonus" working in the *defensive* direction:
the de-saturated proxy now correctly rejects a bad suggestion. But it does NOT
let any correct fix through — live gym recovery is back to 0.0% across the
built-rule perturbations. So the proxy stops the bleeding; it does not close
the arc.

### Bottom line

`GOLDENMATCH_SUGGEST_FULL_DIST=1` is **not ready to flip default-on.** It fixes
the rule-dispatch misfire (the original Finding #2 root cause is confirmed and
addressed at the input layer) but reveals a follow-on bug: the `lower_threshold`
rule's chosen threshold is destructive on the gym's degraded configs, and
full-dist alone trips the oracle precision gate (ncvr 1.00 → 0.00). The cohesion
health proxy + full-dist holds precision at 1.0 but only by suppressing the
suggestion — no live recovery gain. The flag stays default-off (as designed).
Next lever is the `lower_threshold` magnitude / target-threshold logic in the
kernel rule, not the distribution plumbing, which now demonstrably works.

## Findings (dip valley-targeting, 2026-06-25)

Follow-on to the full-dist findings above. The full-dist work proved the
distribution plumbing but exposed a destructive `lower_threshold` magnitude:
on the right-skewed ncvr_synthetic distribution the old global-min `dip()`
returned the **0.04 left-tail sliver** (the gap between the bin-0 non-match
spike and the central hump), so `threshold_too_high` fired
`lower_threshold -> 0.04`, raw recovery **-1231.6%**. This run validates the
right-anchored `dip()` rewrite (`suggest-core/src/diagnostics.rs`, commit
`059f3bca`; native rebuilt in-tree, gym reports `native=0.1.5 sha=059f3bca348a`).
Env: `GOLDENMATCH_SUGGEST_FULL_DIST=1`, `GOLDENMATCH_AUTOCONFIG_MEMORY=0`,
`POLARS_SKIP_CPU_CHECK=1`.

**Headline (FULL_DIST=1 gym, both datasets):** raw gym recovery **+50.5%**
(was **-399.3%**), live gym recovery 0.0% (unchanged — self-verify still
suppresses every built-rule suggestion). The catastrophic raw hole is gone.

Per-perturbation rows of interest (`rec%_RAW` = raw recovery, `rec%_LIVE` =
live recovery; `rule_fired` from the gym board):

| dataset / perturbation | rule_fired | proposed_value | raw recovery | live recovery | suggester_prec |
|---|---|---|---|---|---|
| ncvr_synthetic / `threshold_too_high` | (none — no fire) | n/a (dip=**0.875**, gap to current=0.025 < `DIP_MIN_GAP` 0.05) | **0.0%** (was **-1231.6%**) | 0.0% | **1.00** (was 0.00) |
| ncvr_synthetic / `threshold_too_low` | `raise_threshold` | (raises toward valley) | +93.2% | 0.0% | 1.00 |
| ncvr_synthetic / `bad_freetext_scorer` | (none) | n/a | 0.0% | 0.0% | 1.00 |
| synthetic / `threshold_too_low` | `raise_threshold` | (raises toward valley) | +108.9% | 0.0% | 1.00 |

Oracle no-harm (`report --datasets synthetic,ncvr_synthetic`, FULL_DIST=1):
`suggester_prec` = **1.00 / 1.00** (synthetic / ncvr), `conv_f1` == `base_f1`
on both (0.9887 / 0.9828), `n_sugg=0`.

### Why `threshold_too_high` now fires NOTHING (and that is correct)

The right-anchored `dip()` lands at **0.875** — the trough just below the
true-match mode (bins 22-23, 583+1456 pairs), exactly the spec's measured
target — NOT the 0.04 sliver. The `threshold_too_high` perturbation raises the
ncvr threshold to **0.90**, which is already within `DIP_MIN_GAP` (0.05) of the
0.875 valley (`|0.875 - 0.90| = 0.025`). So the dip rule correctly declines to
move it, and the other threshold rules don't apply (`mass_above` is ~0.2% of
candidate pairs, far below the 0.90 "everything matches" floor; no weak/oversized
clusters trigger the recall-risk band). The kernel emitting NO suggestion here is
the right call: the perturbed config is essentially already at the correct
operating point, and the prior `-1231.6%` "recovery" was the harm of forcing it
to 0.04. (Rust unit tests `dip_targets_valley_below_match_mode_on_right_skewed`,
`dip_clean_bimodal_returns_mid_valley`, `dip_single_mode_returns_none` pin
dip=0.875 / 0.5 / None respectively; the whole `suggest-core` crate is green.)

The symmetric `threshold_too_low` case still fires `raise_threshold` correctly
on both datasets (raw +93.2% ncvr / +108.9% synthetic), so the rewrite did not
break the raise direction.

### Verdict against the kill criterion

The kill criterion (plan Task 3 Step 6): continued investment is earned only if,
across BOTH datasets, (a) `threshold_too_high` recovery materially improves vs
the -1231%/-399.3% baseline AND (b) `suggester_prec` holds ~1.0.

- **(b) PRECISION HELD: PASS.** `suggester_prec` = 1.00 / 1.00 (was 1.00 / **0.00**
  under full-dist-alone). The net-negative suggestion that tanked ncvr precision
  is gone.
- **(a) RECOVERY: this is the honest split.** The destructive collapse is FIXED —
  `threshold_too_high` raw recovery goes from **-1231.6% → 0.0%** and the raw gym
  headline from **-399.3% → +50.5%**. But the improvement is "stopped the
  catastrophe," NOT "delivered a positive recovery on `threshold_too_high`": the
  kernel now correctly emits no suggestion (0.0%) rather than a harmful one, because
  the 0.90 perturbed threshold is already at the valley. There is no *positive*
  recovery win on `threshold_too_high` to bank — the win is the correctness of NOT
  collapsing the threshold.

**Honest bottom line:** the fix is a **correctness win, not an accuracy win.** It
removes the threshold-collapse pathology (the dip lands at the true 0.875 valley,
precision holds at 1.0, the -1231% / -399.3% hole is closed) without producing a
positive live-recovery gain on the built-rule perturbations. Per the kill
criterion's explicit allowance for exactly this outcome — "if it only stops the
catastrophe without a recovery win... that is still a correctness improvement
... but signals the suggestion arc is at diminishing returns and should rest" —
the dip-valley arc has delivered its correctness value and **rests here.** The
flag stays default-off as designed. The remaining gap (live recovery 0.0% — the
self-verify gate suppresses correct fixes) is a property of the gym's degraded
configs being already near-optimal under zero-config #662, not a dip-location
bug, and is out of scope for this plan.
