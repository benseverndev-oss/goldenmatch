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
(`cand_health >= baseline_health`) DROPS it.

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
  few clusters, so a sensitive statistic catches what the mean washes out.
  Candidates (all from the clusters dict the run already emits —
  `confidence` = 0.4·min_edge + 0.3·avg_edge + 0.3·connectivity, `cluster_quality`
  ∈ {strong, weak, split}, `bottleneck_pair`, `pair_scores`):
  - `weak_fraction`: `1 − (#weak clusters / #multi-member clusters)`.
  - `p10_conf`: the 10th-percentile cluster confidence (not the mean).
  - `min_edge`: mean of per-cluster weakest intra-cluster edge.
  Each drops sharply when a few clusters over-merge.
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
  {`weak_fraction`, `p10_conf`, `min_edge`} selects the sensitive statistic, so
  the gym A/Bs all three without a rebuild. The gym-winning statistic becomes the
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
  {`weak_fraction`, `p10_conf`, `min_edge`}, tabulate (live recovery%,
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
