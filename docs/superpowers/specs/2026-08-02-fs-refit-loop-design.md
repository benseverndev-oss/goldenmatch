# FS-aware refit loop — design (Phase 3, threshold-refit first slice)

Status: **IMPLEMENTED (Phase 3a, 2026-08-02)** — the objective below was CORRECTED
by measurement during implementation (see "Implemented objective"). Approved
design; shipped default-OFF behind `GOLDENMATCH_FS_REFIT_THRESHOLD`.
Program: FS/Lever Enablement (`2026-08-01-fs-lever-enablement-design.md`), item 3.

## Implemented objective (2026-08-02) — corrected by measurement

The original design named a "cluster-size knee + mass_above_threshold/dip" health
signal. Measurement during implementation REJECTED the naive forms and produced a
different, validated objective. The negative results are the load-bearing part:

- **Maximize multi-member cluster count** — REJECTED. Peaks at 0.60 on
  historical_50k and would regress F1 0.841→0.756 (it can't tell a correctly
  separated entity from a fragmented true one).
- **Otsu on the scored pairs** — REJECTED as the cut. FS score distributions are
  mode-IMBALANCED (a small false-pair band vs a huge true-match mass); Otsu's
  variance split lands INSIDE the dominant mode (measured 0.88, cutting recall to
  0.62).
- **Bare distributional VALLEY** (density trough, mass on both sides) — recovers
  household but REGRESSED person (−0.06) and ncvr (−0.10): a gap can't distinguish
  an over-merge false band from a gap between low-scoring TRUE matches.

The shipped objective is **valley + two guards** (defense-in-depth; each guard
alone regressed):
1. **Deep-valley gate** — the trough must be NEARLY EMPTY (`< _REFIT_VALLEY_MAX
   = 0.10` of the smaller flank mode). A real class boundary is a near-empty gap
   (household 0.00); ncvr's 22%-of-mode dip inside its corruption-spread match
   distribution is a shoulder, not a boundary → no refit.
2. **Cluster-shape guard** (`fs_refit_link_threshold`) — commit the candidate ONLY
   when re-clustering at it REDUCES over-merge (max cluster size drops) vs the
   default. Household: cutting shrinks giant surname-collapsed clusters (max 8→3)
   → accept. person/ncvr/historical: already right-sized, cutting only drops real
   matches (max unchanged) → reject, keep 0.50.

**MEASURED (dedupe_df, flag on vs off):** household_hardneg F1 **0.947 → 1.000
(+0.053)**; the full panel (febrl3 / ncvr_synthetic / dblp_acm / person /
historical_50k) is **flat, worst ΔF1 +0.0000** (ab_lever GATE PASS). Re-cluster
only (no re-scoring). Tests: `tests/test_fs_refit_threshold.py` (16). Helpers:
`probabilistic.fs_refit_threshold` (pure valley), `_score_distribution_valley`,
`fs_refit_link_threshold` (guarded).

**ROUTE-EXTENSION (2026-08-02).** The refit was initially wired into the default
B2c columnar route only. It now resolves through ONE shared helper
(`pipeline._maybe_refit_link_threshold`, accepting a pair-list OR a
`PAIR_STREAM_SCHEMA` table) called on EVERY FS scoring route: B2c columnar,
arrow-stream, list/batched (the non-native fallback), external-blocks (lsh/ann/
learned/canopy/SN), and out-of-core. Each computes the refit from its own scored
pairs (down to the review cut) before the link/review split — same distribution,
same guarded objective, route-independent. Only the per-block **bench-dump**
diagnostic path keeps the fixed cutoff (it scores per-block for candidate
accounting; the refit needs the whole distribution). **Measured (household_hardneg,
flag on vs off): +0.0529 on ALL of B2c / list-batched / arrow-stream** (the
non-columnar routes previously kept 0.947); **person flat +0.0000 on all** —
no-regression preserved per-route. Route-agnostic contract locked by
`TestMaybeRefitAcrossRoutes` (list==table, flag-off no-op, explicit-threshold
respected, min-pairs guard).

---

## Original design (as approved)

## Problem

The Fellegi-Sunter routing path is **non-iterated**: `auto_configure_probabilistic_df`
builds ONE config — comparison set, blocking, EM weights, and a **fixed link
cutoff** — and commits it. The weighted path runs the controller's refit loop
(profile → detect over/under-merge → refine → re-check) via `RunHistory`; FS
skips all of it. `_fs_link_threshold` resolves to the fixed **0.50** default
unless the caller sets one or EM's opt-in Otsu calibration fires
(`GOLDENMATCH_FS_CALIBRATE_THRESHOLD`, default off, documented to REGRESS on the
controller-refined distribution).

### Measured: the gap is real but shape-dependent

A threshold sweep (score once, re-cluster at each cutoff) quantifies how much F1
the fixed 0.50 leaves on the table:

| dataset | committed 0.50 F1 | oracle-best F1 | headroom | oracle t |
|---|---|---|---|---|
| historical_50k (real) | 0.8456 | 0.8456 | **+0.0000** | 0.50 |
| **household_hardneg (new anchor)** | 0.9471 | 0.9973 | **+0.0502** | 0.70 |

The panel's real datasets are **already 0.50-optimal** (historical_50k peaks
exactly at 0.50 — see the F1/threshold curve, smooth and centered) — which is
precisely why the prior per-lever candidates (honorific, FS-NE) measure-declined:
FS auto-config is at ceiling there. But 0.50 is **not universally** optimal. On
**household hard-negatives** — distinct people sharing a surname (family /
co-residence) who differ on first_name + dob — the shared surname co-blocks the
non-match pairs and the FS scorer places them just below the true duplicates, so
the fixed 0.50 **over-merges** and the F1-optimal cutoff sits at ~0.70. This is a
common, realistic ER failure mode, and the non-iterated path can't see it.

The severity is a knob, not a fixed pathology: heavier household field-overlap
(surname + city + street) drives 0.50 to F1 ~0.06 (P ~0.03) with giant
surname-collapsed clusters (max cluster 97 vs 5 at the oracle) — an over-merge
observable **purely from cluster shape, no labels**. The committed
`household_hardneg` anchor uses the *moderate* shape (surname-only overlap,
+0.05 headroom, P 0.90 → 0.99) so it's a credible target, not a strawman.

## The observable signal (labels-free)

The refit loop must decide without ground truth. The over-merge has a clear
unsupervised signature, all already computed by the existing instrumentation
(`ScoringProfile` / `ClusterProfile` in `core/complexity_profile.py`):

- **Cluster shape** — over-merge inflates `max`/`p95`/mean cluster size and the
  oversized-cluster count; at the healthy cutoff these collapse (max 97→5, mean
  11→2.3 in the severe shape; a subtler max/mean shift in the moderate one).
- **Score-distribution** — `mass_above_threshold` is high and `dip_statistic`
  low (weak bimodality) when the cutoff sits inside the non-match mass; both
  improve as the cutoff moves onto the class boundary.

The **knee** where max/mean cluster size drops sharply *without* the cluster
count exploding (which would mean true matches are being cut → recall loss) is
the healthy operating point.

## Scope — Phase 3a: threshold-refit only

Deliberately the **smallest** slice of FS iteration, because it's the cheapest
and the measurement points straight at it:

- After EM + ONE FS scoring pass, the pipeline holds the scored pairs. **Re-cluster
  only** (filter pairs ≥ t + union-find) across a candidate grid — scoring, the
  expensive part, is NOT repeated, so the whole loop is near-free.
- For each candidate t, compute the labels-free health score above.
- Commit the t with the healthiest shape.

Blocking / comparison-set / EM refit (the rest of what the weighted controller
does) are **later phases** (3b/3c), gated on their own measured targets.

## Guard — must not regress the 0.50-optimal datasets

The documented Otsu failure (regresses on refined configs) came from a *static*
training-pair calibration blind to the resulting distribution. This loop is
different in the two ways that matter:

1. It scores on the **actual scored-pair + cluster distribution**, not a training
   sample.
2. **Health-monotonicity floor**: it commits a non-0.50 cutoff ONLY when that
   cutoff's health strictly beats 0.50's by a margin; otherwise it keeps 0.50.
   So on a 0.50-optimal dataset (historical_50k, febrl3, ncvr, person, dblp) the
   loop is a **no-op** — byte-identical output — because no candidate is healthier.

This makes the loop recall-safe and regression-proof on the existing panel by
construction; its only effect is to *raise* the cutoff off 0.50 where the shape
demands it.

## Validation

- **Recovers the target**: `household_hardneg` F1 0.947 → ~0.997 (the +0.05 the
  fixed cutoff leaves on the table), driven to the oracle knee by the health
  signal alone.
- **No panel regression**: historical_50k / febrl3 / ncvr_synthetic / person /
  dblp_acm stay at their committed-0.50 F1 (the guard keeps 0.50 — the loop
  proves it's already the knee). An `ab_lever`-style gate for the loop's flag
  asserts both.
- Ships default-OFF behind a flag (`GOLDENMATCH_FS_REFIT_THRESHOLD` or similar);
  default byte-identical. Flip only after the panel + household gate prove it,
  per the domain-comparators / v2 precedent.

## Phasing

- **3a (this design)** — health-gated threshold re-cluster loop + the
  `household_hardneg` anchor as its standing target. One PR.
- **3b** — blocking / comparison-set refit on a measured target (needs a shape
  where the *blocking*, not the threshold, is off — a separate hunt).
  - **MEASURED-DECLINED (2026-08-02).** The hunt for an off-*blocking* target came
    up empty for a STRUCTURAL reason: **auto-config already emits a blocking pass
    per field** (a 7-pass `multi_pass` on person data), so a true pair co-blocks
    via *some* pass as long as it shares *any* stable field — committed blocking
    recall is **1.0** on every constructed adversarial shape (corrupt one name,
    corrupt both names, heavy surname corruption). The only real under-blocking
    case — a field the classifier MISCLASSIFIED (e.g. `birth_place`→`name`) — was
    already solved by the merged `GOLDENMATCH_FS_ORTHOGONAL_BLOCKING` lever. Every
    residual failure on those shapes was **over-merge (precision 0.24–0.89, recall
    still 1.0)** — 3a's threshold domain, not blocking. On real historical_50k the
    same holds: LOWERING the threshold RAISES recall (0.777→0.890 at link 0.30),
    proving the missing recall is pairs that ARE co-blocked but scored below the
    cut — scoring, not coverage. A blocking refit can only *add* an unused field,
    and there is none to add (auto-config used them all); the inverse move —
    *pruning* noisy passes for precision — already exists opt-in
    (`blocking_pass_selection.py`) and is better served by the threshold loop (3a).
    Net: the FS refit-loop value was **entirely in the threshold slice (3a)**;
    blocking is already iterated at build time. Not built.
- **3c** — fold the FS loop into the controller's `RunHistory` / commit
  machinery so FS and weighted share one iteration surface (the "one iteration
  surface" unification; largest, last).
  - **REFRAMED + delivered as OBSERVABILITY (2026-08-02).** The literal framing is
    a **conceptual mismatch**: `auto_configure_probabilistic_df` is *non-iterative
    by design* (it does NOT run `AutoConfigController`; 3a proved the FS config is
    already good), and the refit is a *scoring-time* threshold adjustment in
    `pipeline._maybe_refit_link_threshold`, not a config iteration — whereas
    `RunHistory` is a config-iteration audit trail (propose → profile → refine →
    commit). Forcing the refit into `RunHistory` would mean either re-architecting
    FS to iterate configs (contradicts the deliberate non-iterated design) or
    shoehorning a scoring-time decision into a config-time structure — a refactor
    with **no F1 delta and negative structural value**. The genuine intent behind
    "one surface" is **auditability**: the refit silently moved the link cutoff
    (0.50→0.70) on an opt-in path with no record. Delivered by making the decision
    OBSERVABLE on the SAME logging surface the controller uses for its commit
    decision — `fs_refit_link_threshold` now logs INFO on commit (`0.50 -> 0.70`,
    valley candidate, max cluster `N -> M`, over-merge reduced) and DEBUG on
    decline/no-op. Return behavior byte-identical (log-only); the FS refit is the
    non-iterated path's analogue of a `RunHistory` decision, now on one surface.
    Tests: `TestRefitDecisionLogged`. The deeper `RunHistory` merge is DECLINED as
    a mismatch, not deferred.

## Risks / non-goals

- Labels-free health can be fooled by adversarial shapes; the health-monotonicity
  floor + default-OFF flag bound the blast radius. Not a replacement for a real
  label budget where one exists.
- Not attempting per-field or EM refit here — threshold only.
- The `household_hardneg` anchor is NOT added to the committed-baseline gates
  (gym/scorecard `REGISTRY`) in this step — only the loader exists — to avoid
  disturbing their blessed baselines; the 3a implementation wires it into the
  loop's own gate.
