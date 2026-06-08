# Landscape-sculpting ER vs discrete split/merge — kill-criterion results

Runner: `scripts/research/landscape_er.py`. numpy + the existing real-data
loaders; real Febrl3 / DBLP-ACM subsamples + a synthetic multi-source-conflict
regime. 2026-06-07.

> **Verdict: FAIL — the mechanism is COSMETIC.** Under a fair comparison with a
> *calibrated* objective, the landscape loop produces the **same partition** as a
> discrete split/merge loop, and a plain threshold often beats both. An earlier
> apparent win was a θ-calibration artifact (corrected below).

## The test

The prior-art scan found the iterative add/split ER loop is partly anticipated
(pBlocking, Gruenheid, Sayari); the only defensible novelty is the **mechanism**:
sculpting a potential/attractor landscape (carve basins, raise ridges, global
re-flow) instead of discrete graph edits. Kill-criterion: **does the landscape
mechanism beat a discrete split/merge loop optimising the SAME objective on the
SAME graph?** Everything shared except the mechanism (affinity graph,
correlation-clustering objective, Fiedler 2-cut, init, symmetric move sets).

## What happened, in order (the honest trail)

**1. First run — apparent win, but it was an artifact.** With an MDL-ish ledger
and θ = median nonzero affinity, the landscape beat discrete by +0.07–0.145 F1 on
DBLP-ACM across seeds. **This was wrong.** θ=median (≈0.02) was far below the
affinity's signal band, so the objective's optimum was gross over-merge; the
landscape merely chased that broken objective slightly less aggressively. Not a
real mechanism advantage.

**2. Fixed the objective (task a).** Switched to the canonical
**correlation-clustering** objective `S = aff − θ` and a calibrated, unsupervised
θ = mean + 2·std of nonzero affinities (median → over-merge; Otsu → all-singletons;
mean+2std puts connected-components F1 ≥ 0.98 on clean subsamples). With a correct
θ, **clean data is essentially solved by a threshold**, and:

| Clean data (calibrated θ) | CC@θ | discrete | landscape |
|---|---:|---:|---:|
| Febrl3 (40 ent) | — | 0.917 | **0.917 (identical)** |
| DBLP-ACM (40 ent) | — | 1.000 | **1.000 (identical)** |

The mechanism is **cosmetic** — identical partitions.

**3. Built the multi-source-conflict regime (task b).** Distinctive entities +
spurious BRIDGES (shared placeholder values across a few unrelated entities), so
no single θ separates and connected-components should fail. Strong bridges,
3 seeds:

| synth-conflict (60 ent) | CC@θ | discrete | landscape |
|---|---:|---:|---:|
| seed 0 | **0.842** | 0.757 | 0.757 (identical) |
| seed 1 | **0.964** | 0.911 | 0.911 (identical) |
| seed 2 | **0.952** | 0.855 | 0.855 (identical) |

Still **cosmetic** (identical every seed), and the refinement loops *underperform*
a plain threshold (CC@θ) here — the split/merge moves hurt.

## Why it's cosmetic (mechanistic explanation)

Given the **same objective** and the **same split proposals**, a greedy hill-climb
reaches the same local optimum whether a move is expressed as "relabel a cluster"
(discrete) or "raise a ridge + add attractors + globally re-flow" (landscape). The
landscape is a different *route* to the same destination, not a different
destination. Global re-flow changes nothing when the proposals and the acceptance
criterion are shared.

## Verdict: the topology/landscape framing does NOT earn its keep

- **Novel** (the six-angle scan confirmed ER-as-sculpted-landscape is unoccupied)
  but **empirically cosmetic** under a fair, calibrated comparison — the same
  outcome shape as the 1+3+6 arc: novelty validated, competitiveness not.
- The one apparent win was a θ-artifact; fixing the objective erased it.
- Where the affinity is clean, a threshold already solves it; where it has
  conflicts, the *shared objective + shared cut primitive* determine the result,
  not the mechanism.

## What would have to be true for it to matter (not pursued)

The mechanism could only help if it changed the *proposals* or the *objective* —
e.g., the ridge geometry proposing cuts a Fiedler-on-the-discrete-graph wouldn't
find, or the landscape encoding a constraint the correlation-clustering objective
can't. With shared proposals + objective, it cannot. Chasing a hand-crafted
regime where it happens to win would be p-hacking; the disciplined call is to
**close the arc here** with the negative result documented.
