# FS-aware refit loop — design (Phase 3, threshold-refit first slice)

Status: **DESIGN — measurement done, awaiting approval before implementation.**
Program: FS/Lever Enablement (`2026-08-01-fs-lever-enablement-design.md`), item 3.

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
- **3c** — fold the FS loop into the controller's `RunHistory` / commit
  machinery so FS and weighted share one iteration surface (the "one iteration
  surface" unification; largest, last).

## Risks / non-goals

- Labels-free health can be fooled by adversarial shapes; the health-monotonicity
  floor + default-OFF flag bound the blast radius. Not a replacement for a real
  label budget where one exists.
- Not attempting per-field or EM refit here — threshold only.
- The `household_hardneg` anchor is NOT added to the committed-baseline gates
  (gym/scorecard `REGISTRY`) in this step — only the loader exists — to avoid
  disturbing their blessed baselines; the 3a implementation wires it into the
  loop's own gate.
