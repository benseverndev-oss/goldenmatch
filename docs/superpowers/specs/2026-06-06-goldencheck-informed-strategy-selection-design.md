# GoldenCheck-informed matching-strategy selection (collision-aware auto-config)

**Date:** 2026-06-06
**Status:** design (pre-spec-review)
**Scope:** `packages/python/goldenmatch/goldenmatch/core/autoconfig*` (candidate
generation, the demote-clustered-identity rule, NE promotion), an optional
GoldenCheck profiling signal, and the controller's commit/fallback loop.
**Motivated by:** the DQbench ER **T3 precision collapse** (composite 51.56;
T3 F1 0.257, precision **14.9%**), diagnosed 2026-06-06 against the real tier
data (`scripts/dump_dqbench_er_tiers.py`).

## Context — what the diagnosis actually found

Running zero-config on the real DQbench T3 (10k rows, 2k adversarial dupes):

| config (T3) | P | R | F1 | biggest cluster |
|---|---|---|---|---|
| zero-config (normal) | 0.149 | 0.915 | 0.257 | huge |
| zero-config (thinking) | 0.149 | 0.915 | 0.257 | huge |
| fuzzy name+address only (no exact email/phone) | 0.885 | 0.636 | 0.740 | 7 |

The committed config blocks on `zip` and emits **exact_email + exact_phone +
weighted(name,address)** — all three with NE already promoted. The precision
collapse is **not** a missing strategy:

1. **NE is already on** (eager `promote_negative_evidence`) — it isn't the gap.
2. **More iterations don't help** — `normal` == `thinking` == `einstein` == 0.257.
   It's a *strategy* problem, not an iteration-budget problem.
3. **The FP smoking gun:** false-positive pairs share **nothing**
   (`shared=[]`). That's **transitive cluster explosion** — A~B on email,
   B~C on phone, Union-Find collapses the component.
4. **The collision driver:** `phone` has one value shared by **170 distinct
   rows** (1,432 phones and 2,345 emails are shared overall). The `exact_phone`
   matchkey hard-merges all 170 → ~14k FP pairs from a single value.
5. **`rule_demote_clustered_identity` misses it:** phone is 84% unique overall,
   so an *average* collision-rate metric reads "low collision" while a heavy-tail
   value (170-way) does all the damage.

**Dropping the exact identifiers recovers F1 0.257 → 0.740.** The recall cost
(0.915 → 0.636) is real — some true dupes agree *only* on email/phone — which is
exactly the **Fellegi-Sunter** case: keep the identifier's signal but
**down-weight it by its `u`** (random-agreement probability) instead of
hard-merging. So the fix is collision-aware *strategy selection*, not "add NE".

**Honest caution (measured):** naive "adversarial → probabilistic" can *hurt* —
on a different (email-collision) fixture, forced probabilistic scored 0.260 vs
zero-config 0.919. So the trigger must be specific and the switch must be
**measured with a fallback**, never blind.

## Decisions that shape this design

1. **The trigger is collision detection, not "adversarial".** Detect
   **heavy-tail value collisions** on candidate identifier columns (the blast
   radius of the worst value), surfaced by GoldenCheck profiling — not an average
   collision rate.
2. **The response is demote-exact → probabilistic/weighted, measured.** A
   collision-prone identifier becomes a *down-weighted* Fellegi-Sunter / weighted
   contributor (keeps recall signal, drops the hard-merge), added as a controller
   **candidate** and committed only if it scores better. The deterministic
   exact-only config stays the fallback.
3. **A cluster-explosion guard is the safety net.** Independent of strategy: cap
   / flag clusters whose size is dominated by a single collision-prone field, so
   one bad value can never collapse a 170-way component.

## Approach (phased, load-bearing-first)

### Phase 1 — heavy-tail collision signal (the trigger)
Add a per-column **collision blast radius** to profiling: `max_value_count` and
`collision_mass` (fraction of rows whose identifier value is shared by ≥ K
others). Source it from GoldenCheck's duplication/uniqueness profilers when
GoldenCheck is installed; fall back to a cheap in-`goldenmatch` `value_counts`
heavy-tail probe otherwise (no hard GoldenCheck dependency). This is the metric
`rule_demote_clustered_identity` should have used.

**Gate:** on T3, the signal flags `phone` (and `email`) as collision-prone; on a
clean-identifier dataset (e.g. DBLP-ACM `id`) it does not.

### Phase 2 — collision-aware demotion + probabilistic candidate
When an exact-matchkey identifier is collision-prone:
- **Demote** it out of the exact matchkey (it must not hard-merge), and
- **Add a probabilistic / weighted candidate** that includes it as a
  down-weighted field (F-S `u` self-regulates the 170-way phone).
Both the demoted-exact and the probabilistic configs enter the controller's
candidate set; the controller commits the best by proxy (B³ / mass separation),
**falling back** to the deterministic config when the candidate doesn't win.

**Gate:** T3 F1 ≥ 0.74 (the fuzzy-only floor) without regressing the clean
canonical sets (DBLP-ACM 0.9641, Febrl3 0.9665) — measured via
`run_benchmarks.py --planning-effort` A/B on the dumped tiers.

### Phase 3 — cluster-explosion guard
Extend the oversized-cluster / weak-cluster logic to detect a cluster whose
connectivity is carried by a single collision-prone field and split/flag it
(bound the blast radius even if a bad exact matchkey slips through).

**Gate:** no T3 cluster exceeds a sane cap; `shared=[]` FP pairs drop to ~0.

## Why this is the right shape (ties to existing work)
This is the **"widen the vocabulary + GoldenCheck-informed candidate generation,
measure, fall back"** work already staged behind the `thinking`/`einstein` seam
in `2026-06-06-autoconfig-search-strategy-after-engine-speedup-design.md` §Phase
2/3. It reuses the eager-NE machinery, the `rule_demote_clustered_identity`
slot (fixing its metric), `build_probabilistic_matchkeys` /
`rule_select_probabilistic_matchkey`, and the controller's commit/fallback loop.
No new subsystem.

## Verification
- **Diagnosis reproducer:** `scripts/dump_dqbench_er_tiers.py` dumps the real
  tiers; the T3 table above is the regression target.
- **Phase gates** as above; A/B every tier at `normal` vs the new candidate via
  the `--planning-effort` runner.
- **Non-regression:** DBLP-ACM 0.9641 + Febrl3 0.9665 must not move (their
  identifiers aren't collision-prone, so the trigger stays off).

## What this design explicitly does NOT do
- Blindly switch to probabilistic on "adversarial" data (the 0.26 result).
- Add a hard GoldenCheck dependency (the heavy-tail probe degrades in-package).
- Touch the planning-effort tiers or the engine ladder.
- Claim T3 reaches the published 91 — the fuzzy-only floor is 0.74; closing the
  rest (recall on identifier-only dupes) is the probabilistic candidate's job and
  is measured, not assumed.

## References
- Diagnosis: `scripts/dump_dqbench_er_tiers.py` (real T3 data); FP analysis +
  collision counts (phone 170-way), 2026-06-06.
- Existing slots: `core/autoconfig_rules.py::rule_demote_clustered_identity`,
  `compute_identity_collision_signal`, `core/autoconfig_negative_evidence.py`,
  `core/autoconfig.py::build_probabilistic_matchkeys`.
- Parent arc: `2026-06-06-autoconfig-search-strategy-after-engine-speedup-design.md`.
