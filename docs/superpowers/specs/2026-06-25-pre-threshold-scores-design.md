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
