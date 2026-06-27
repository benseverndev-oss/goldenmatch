# Self-Verify Health-Proxy Redesign — Cohesion × Coverage — Design

**Date:** 2026-06-24
**Status:** Approved (brainstorm); pending plan
**Author:** Ben Severn (with Claude)
**Follows:** `2026-06-24-config-suggestion-kernel-design.md` (Plan 1, self-verify),
`2026-06-24-suggester-gym-design.md` (the gym that exposed this)

## Problem

The gym's first measurement: with self-verify ON (production), the config
suggester recovers **0%** of a damaged config. The rules are mechanically capable
(verify-OFF "raw" recovers `threshold_too_low` fully on synthetic, +72% on
ncvr_synthetic) — but the self-verification health proxy suppresses the correct
fix, so it never reaches the user.

### Root cause (confirmed with instrumentation)

The proxy is `suggestion_health_from_clusters = matched_rate * avg_conf -
HHI_penalty`. Decomposing the `threshold_too_low` case on synthetic
(`scripts`-side instrumentation, 706 records):

| config | F1 | health | matched_rate | avg_conf | HHI penalty |
|---|---|---|---|---|---|
| degraded (over-merged) | 0.901 | **0.7718** | 0.788 | 0.980 | 0.000 |
| fixed (threshold raised) | 0.990 | **0.7624** | 0.764 | 0.999 | 0.000 |

The fix **raises F1 +0.088** but the proxy **drops −0.009**, so the verify gate
DROPS it. (The gate is `cand_health >= baseline_health - _VERIFY_EPS` with
`_VERIFY_EPS = 1e-6` (`adapter.py`) — negligible; the −0.009 drop is ~9000× the
slack, so EPS is not a contributor and the diagnosis is unaffected.)

The mechanism is NOT simply "matched_rate is bad" — the proxy is already a
precision×recall product. The bug is that **the precision factor (`avg_conf`) is
saturated and insensitive**: over-merge folds a few weak edges into a handful of
clusters, but averaged across ~226 clusters that barely moves the mean
(0.999→0.980, −1.8%), while matched_rate moves more (−3%). The recall factor
dominates, so the proxy *ramps* toward more-matching with no precision
counterweight that fires on ordinary (non-collapse) over-merge — the HHI penalty
only catches a few-giant-clusters pathology, which is 0.000 here. Net: the proxy
is recall-biased and structurally suppresses every precision-improving
(match-reducing) suggestion. The same design that made self-verify *safe* (no
net-negative) made it *useless* on degraded configs.

### Scope

In scope: redesign the cluster-based health proxy so it has a sensitive
precision signal, gated and proven on the gym + oracle.

Out of scope (tight sibling follow-up, separate spec): the rule-misfire — the
kernel emits `raise_threshold` for `threshold_too_high` and `bad_freetext_scorer`
(raw recovery goes negative), i.e. `lower_threshold` / `swap_scorer` don't fire
on degraded configs. That is a different root cause (kernel rule triggering), and
the proxy is the dominant blocker (live = 0 even where the correct rule fires).

Also out of scope: the default-on flip (`legacy`→`cohesion`). This spec delivers
the proxy + the gym/oracle-proven candidate behind a flag; flipping the default
is a separate, evidence-backed change (same posture as Plan 1).

## Approach

Three were considered:

- **A — Precision-leaning cluster proxy (chosen).** Keep the cluster-based shape
  but replace the saturated `avg_conf` with a precision-SENSITIVE cohesion
  statistic computable from existing run outputs. Buildable now; measurable on
  the gym in one run.
- **B — Score-separation proxy.** Health = bimodality gap of the pair-score
  distribution; peaks correctly. But needs the full PRE-threshold candidate score
  distribution, which the pipeline doesn't emit today (only pairs ≥ threshold —
  health.py's docstring flags this). Real plumbing; deferred.
- **C — Weak-pseudo-label pseudo-F1.** Generate pseudo-positives (pairs agreeing
  on ≥2 discriminative fields) + pseudo-negatives (random cross-block pairs),
  estimate pseudo-precision/recall. Most powerful, structurally immune to the
  bias; most work + noisy labels. The escalation if A can't thread both
  constraints.

**Decision: A.** The gym makes it empirical — build the precision-sensitive
proxy, sweep the cohesion statistic, and read live-recovery + suggester-precision
off the scoreboards. Escalate to C only if A disappoints.

## The new formula

```
health = cohesion × coverage
  cohesion = a PRECISION-SENSITIVE cluster statistic   (replaces saturated avg_conf)
  coverage = matched_rate, saturating                  (recall floor / under-merge guard)
```

- **cohesion** is the load-bearing change. Over-merge *concentrates* damage in a
  few clusters, so the discriminating signal lives in the **low tail** of
  intra-cluster edge strength — NOT the mean, NOT p10, NOT the coarse
  `cluster_quality` flag (all empirically dead on the synthetic over-merge; see
  Grounding findings). The candidate statistics to sweep (all from the clusters
  dict the run already emits — `confidence`, `cluster_quality`, `bottleneck_pair`,
  `pair_scores`; per-cluster min edge = `min(pair_scores.values())` /
  `pair_scores[bottleneck_pair]`):
  - `min_edge`: the GLOBAL minimum intra-cluster edge across all multi-member
    clusters (degraded 0.66 vs clean 0.80 — clear signal).
  - `mean_bottomk_edge`: mean of the k weakest per-cluster min-edges (smooths the
    single-min noise; k a small constant, e.g. 5, or a small fraction).
  - `edge_below_cutoff_fraction`: `1 − (#clusters with min-edge < CUTOFF) / #mm`,
    a CONTINUOUS weak-fraction with a tunable edge cutoff (e.g. 0.75) — NOT the
    coarse `cluster_quality == "weak"` flag, which never trips here.

  **Grounding findings (planning, synthetic `threshold_too_low`, 706 records):**
  `conf[p10]` and `minedge[p10]` are saturated at **1.000** for both degraded and
  clean (>90% of clusters perfect), and all clusters read `cluster_quality ==
  "strong"` — so `p10_conf` and coarse `weak_fraction` carry NO signal. The
  global MIN does: conf-min 0.678 (degraded) vs 0.790 (clean); minedge-min 0.657
  vs 0.800. Product check confirms the fix: `min_edge` cohesion gives degraded
  `0.788×0.657=0.518` < clean `0.764×0.800=0.611` → **fix KEPT** (vs the old
  mean: `0.772 > 0.763` → fix dropped, the bug). So a low-tail edge statistic
  flips the verdict correctly on the case that motivated this work.
- **coverage** keeps the under-merge guard: as a threshold rises too far and
  matches vanish, `matched_rate → 0 → health → 0`. Saturating (capped) so it
  stops *rewarding* volume past a reasonable point — the peak lands in the middle.
- **Shape:** a product (or `min`) so health is high only when BOTH cohesion and
  coverage are high → a real PEAK at the right operating point, not a ramp.
  Over-merge → cohesion collapses → health down (fix kept). Under-merge →
  coverage collapses → health down.
- The exact cohesion statistic is chosen by a **gym sweep**, not a guess.

## Architecture & gating

Drop-in internals swap behind the existing verify gate; default-off until proven.

- **Same interface.** `adapter.py`'s verify gate calls
  `suggestion_health_from_clusters(clusters, n_records)` and keeps a suggestion
  iff `cand_health >= baseline_health`. That call site/signature is UNCHANGED. Add
  a new implementation `suggestion_health_cohesion(clusters, n_records)` in
  `health.py` + a thin selector.
- **Gated by env, default = legacy.** `GOLDENMATCH_SUGGEST_HEALTH` ∈
  {`legacy` (default), `cohesion`}. Default `legacy` ⇒ PR #1267's shipped
  behavior is **byte-identical** until a separate flip. The gym/oracle runs set
  `cohesion`.
- **Cohesion sub-switch for the sweep.** `GOLDENMATCH_SUGGEST_COHESION` ∈
  {`min_edge`, `mean_bottomk_edge`, `edge_below_cutoff_fraction`} selects the
  low-tail statistic, so the gym A/Bs all three without a rebuild. The gym-winning statistic becomes the
  hardcoded default inside `cohesion`; keep or drop the sub-switch at flip time
  (YAGNI).
- **One focused file.** All of this lives in `health.py` (formula + two
  selectors). No change to the adapter, kernel, or gym — the gym already drives
  both `verify=True`/`verify=False`, so pointing it at the new proxy is just an
  env var.

Unit boundary stays clean: `health.py` owns "how healthy is this config,"
`adapter.py` owns "keep or drop," the gym owns "did it help."

## Validation (the dual gate)

The redesign is "done" only when it threads BOTH constraints, both already
measurable:

- **Gym (the unblock):** `GOLDENMATCH_SUGGEST_HEALTH=cohesion` → `gym` board.
  Success = live recovery% rises — `threshold_too_low` goes from 0% toward its
  raw ceiling (synthetic +109%, ncvr +72%) WITH `expected_rule_fired_live=True`.
- **Oracle (the no-harm guard):** same env → oracle `gate`. Success =
  `suggester_precision` stays ~1.0 on synthetic/ncvr_synthetic (the new proxy did
  not reopen the net-negative door self-verify closed). This is the BINDING
  safety constraint — recovery that reintroduces harm is a regression.
- **The sweep is the experiment:** run the gym for each of
  {`min_edge`, `mean_bottomk_edge`, `edge_below_cutoff_fraction`}, tabulate (live recovery%,
  suggester_precision) per candidate, pick the one maximizing recovery subject to
  precision ≈ 1.0. Record the table in a findings note appended to this spec.

## Testing

- **Pure unit tests** (no native) for `suggestion_health_cohesion`: an
  over-merged clusters dict (a few weak clusters) must score LOWER than a clean
  dict with the SAME matched_rate — the exact inversion the legacy proxy gets
  wrong. Plus: recall-collapse (nothing matched) → low; under-merge (high
  cohesion, low coverage) → below the balanced peak; each cohesion sub-statistic
  computed correctly.
- **Selector tests:** `legacy` is byte-identical to today; `cohesion` routes to
  the new formula; the cohesion sub-switch selects the right statistic.
- **Gym + oracle runs** are the integration validation (recorded in the findings
  note), not pytest assertions.

## Done criteria

- `suggestion_health_cohesion` + the two env selectors land in `health.py`;
  `legacy` default keeps #1267 byte-identical (selector test proves it).
- Unit tests pin the over-merge inversion fix (over-merged scores below clean at
  equal matched_rate) and each cohesion statistic.
- A gym sweep over the three cohesion statistics is run; the winner shows
  `threshold_too_low` live recovery rising from 0% with the right rule firing,
  while the oracle holds `suggester_precision ≈ 1.0`. Numbers recorded in a
  findings note appended here.
- No default behavior change (default stays `legacy`); the default-on flip is a
  separate evidence-backed change.

## Findings (gym sweep, 2026-06-24, sha 29345d58)

Sweep ran across all four variants on `synthetic` (373 rows, 218 gt_pairs) and
`ncvr_synthetic` (7500 rows, 2500 gt_pairs). All runs used
`GOLDENMATCH_NATIVE=0` (pure Python; native not needed for the proxy logic).

### Gym board (threshold_too_low perturbation focus)

| variant | live rec synthetic | rule_fired synthetic | live rec ncvr | rule_fired ncvr | gym score (live) |
|---|---|---|---|---|---|
| legacy | 0.0% | no | 0.0% | no | 0.0% |
| cohesion / min_edge | 108.9% | yes | 72.3% | yes | -225.9% |
| cohesion / mean_bottomk_edge | 108.9% | yes | 72.3% | yes | -225.9% |
| cohesion / edge_below_cutoff_fraction | 108.9% | yes | 72.3% | yes | -225.9%* |

*`edge_below_cutoff_fraction` errored on `ncvr_synthetic/flattened_weights` (OOM
on a 95x95 array in an unrelated code path, not the proxy); the
`threshold_too_low` row was clean.

The headline finding for `threshold_too_low`: **all three cohesion statistics
unblock recovery completely** (legacy 0.0% -> 108.9% / 72.3%), with the correct
`raise_threshold` rule firing on both datasets. This confirms the on-paper math
from the spec: the degraded config's proxy score (0.518) < the fixed config's
score (0.611), so the gate passes the fix.

### Oracle (suggester_precision no-harm check)

| variant | sugg_prec synthetic | sugg_prec ncvr | passes no-harm? |
|---|---|---|---|
| legacy | 1.00 | 1.00 | yes (0 suggestions) |
| cohesion / min_edge | 1.00 | 0.00 | **NO** |
| cohesion / mean_bottomk_edge | 1.00 | 0.00 | **NO** |
| cohesion / edge_below_cutoff_fraction | 1.00 | 0.00 | **NO** |

**All three cohesion statistics fail the oracle no-harm constraint on
ncvr_synthetic.** `suggester_prec = 0.00` on ncvr means the proxy is accepting
suggestions that do NOT improve F1 (rank_corr = -1.000 on the oracle report).
The oracle `n_sugg=2` for ncvr vs `n_sugg=0` (legacy) shows the cohesion proxy
passes suggestions the legacy proxy correctly suppressed.

### Dual-gate verdict

**NO statistic threads both constraints on this gym/oracle pairing.**

- Gym half (live recovery): PASS for all three cohesion variants.
- Oracle half (no-harm / suggester_precision): FAIL for all three cohesion variants.

The cohesion proxy unblocks `threshold_too_low` recovery as predicted, but it
simultaneously re-opens the net-negative door on ncvr_synthetic: the proxy is
now too permissive and accepts suggestions that hurt F1. This is the opposing
failure mode to the legacy proxy (which was too strict). The design's "peak at
the right operating point" hasn't been achieved with cohesion x coverage alone.

### Why the no-harm failure happens

The cohesion x coverage proxy has no mechanism to penalize suggestions that
LOWER the threshold (over-merge direction). When the ncvr suggester fires
`lower_threshold` or similar rules, the resulting over-merged config can
still have high cohesion (high-confidence merges dominate) and high coverage
(more records matched), so the proxy score RISES even when F1 falls. The legacy
proxy had the same property conceptually but its `matched_rate * avg_conf`
formula happened to suppress those suggestions via the recall-sensitivity of
`matched_rate` in this dataset. The cohesion proxy, with saturating coverage
(capped at 0.30 = 30% of records matched), saturates faster and allows the gate
to open on over-merge.

### Recommendation: escalate to Approach C (weak-pseudo-labels)

Per the design's escalation criterion: "If NO stat threads both constraints, say
so HONESTLY and recommend escalation to Approach C (weak-pseudo-labels)."

Approach C generates pseudo-positives (pairs agreeing on >=2 discriminative
fields) + pseudo-negatives (random cross-block pairs) to estimate a
pseudo-precision and pseudo-recall. This approach is structurally immune to the
bias that bites both the legacy proxy (recall-only, saturated precision) and
the cohesion proxy (precision-sensitive on the over-merge tail, but not
directional -- it can't tell "fewer correct merges" from "fewer wrong merges"
without reference pairs). The pseudo-label layer adds the directional signal
both proxies lack.

The cohesion x coverage proxy is a real improvement on the specific
`threshold_too_low / over-merge` pathology the design targeted, but the
ncvr_synthetic oracle failure means it cannot be flipped default-on safely.
Approach C is the next step.

## Open questions for planning

- Confirm the clusters dict reliably carries `confidence` and `cluster_quality`
  (or `quality`) for the multi-member clusters the proxy reads — and the exact
  key names as emitted by the engine path the gym/adapter use
  (`EngineResult.clusters`). Pin the weak-cluster detection (`quality == "weak"`)
  against the real values.
- Decide the `coverage` saturation form (e.g. `min(matched_rate / CAP, 1.0)` with
  a CAP, vs a smooth squash) during planning — pick the simplest that yields a
  peak, validated on the gym.
- Confirm whether `min_edge` is directly available per cluster (`bottleneck_pair`
  score / `pair_scores` min) or must be derived, so the `min_edge` candidate is
  cheap.
- `suggestion_health_from_clusters` currently EXCLUDES `oversized` clusters before
  computing stats, but over-merge can manifest as an oversized cluster (later
  auto-split). Planning must confirm the over-merge signal the cohesion stat needs
  survives the `oversized` exclusion, or decide whether the cohesion statistic
  should include oversized clusters too (else the very pathology we want to catch
  is filtered out before the proxy sees it).
