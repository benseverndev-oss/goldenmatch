# Landscape-sculpting ER vs discrete split/merge — kill-criterion results

Runner: `scripts/research/landscape_er.py`. numpy + the existing real-data
loaders; real Febrl3 / DBLP-ACM subsamples (70 entities), 3 seeds. 2026-06-07.

```bash
python scripts/research/landscape_er.py --dataset dblp-acm --max-entities 70 --seed 0
```

## The test

The prior-art scan found the iterative add/split ER loop is partly anticipated
(pBlocking, Gruenheid, Sayari), so the only defensible novelty is the
**mechanism**: sculpting a potential/attractor landscape (carve basins, raise
ridges, global re-flow) instead of discrete graph edits. Kill-criterion: **does
the landscape mechanism beat a discrete split/merge loop that optimises the SAME
objective on the SAME graph?** Everything is shared except the mechanism — same
IDF-token affinity graph, same MDL-style cost ledger, same Fiedler 2-cut, same
init, and both have split + merge (+ carve / move) moves. Only the mechanism
differs: landscape routes marbles by clamped label-propagation and splits by
raising a ridge (zeroing cut edges) + **global re-flow**; discrete relabels
directly with no terrain and no re-flow.

## Results (3 seeds each)

| Dataset | seed | discrete F1 | landscape F1 | Δ | clusters disc / land / true |
|---|---|---:|---:|---:|---:|
| Febrl3 (PII) | 0 | 0.888 | 0.906 | +0.018 | 47 / 56 / 70 |
| Febrl3 | 1 | 0.840 | 0.831 | −0.009 | 41 / 48 / 70 |
| Febrl3 | 2 | 0.875 | 0.889 | +0.014 | 47 / 58 / 70 |
| **DBLP-ACM** (bibliographic) | 0 | 0.752 | **0.897** | **+0.145** | 54 / 65 / 70 |
| **DBLP-ACM** | 1 | 0.826 | **0.898** | **+0.072** | 58 / 64 / 70 |
| **DBLP-ACM** | 2 | 0.840 | **0.917** | **+0.077** | 58 / 63 / 70 |

Means: Febrl3 discrete 0.868 vs landscape 0.875 (**+0.008, a wash**); DBLP-ACM
discrete 0.806 vs landscape 0.904 (**+0.098, consistent across all seeds**).

## Verdict: PASS — the mechanism earns its keep, regime-specifically

1. **On bibliographic data the landscape mechanism clearly and consistently beats
   the discrete loop** (+0.07 to +0.145 F1, every seed). On PII it's a wash.
2. **It wins F1 while reaching HIGHER cost than discrete.** Discrete optimises the
   shared objective *more* (lower bits) but over-merges; the landscape's global
   re-flow lands closer to the true cluster count (DBLP 63–65 vs discrete 54–58,
   true 70) and scores higher F1. So the advantage is an **inductive bias**: the
   ridge-raising + global re-flow **resists over-merging**, which is exactly the
   failure mode DBLP-ACM's shared title/venue vocabulary induces (cf. the step-1
   finding that bibliographic data over-merges under naive affinity).
3. This is a genuine mechanistic difference the discrete loop **structurally
   cannot reproduce** — its local relabel can't re-route the rest of the graph
   when a barrier goes up.

## Honest caveats

- **Small subsamples** (N≈120–184, 70 entities), 3 seeds — promising, not
  conclusive. Needs full-scale runs and more seeds with CIs.
- **The shared objective is still imperfect** (both undershoot the true cluster
  count; discrete reaches lower bits than gold). The landscape "wins" partly by
  NOT fully optimising a miscalibrated objective — a better objective could
  change the gap in either direction. A cleaner objective is the next dependency.
- **Regime-specific**: no benefit on PII. The scan predicted the mechanism should
  matter most where over-merge/conflict is severe; bibliographic data is a mild
  version of that, genuine multi-source conflict would be the real test.
- vs. the existing GoldenMatch zero-config controller (DBLP-ACM F1 0.964), this
  prototype (≈0.90 on a 70-entity subsample, untuned) is not yet competitive in
  absolute terms — but unlike the 1+3+6 arc it shows a real per-mechanism edge.

## Next levers (if pursued)

1. **A better-calibrated objective** (the current MDL surrogate's optimum sits
   below the true cluster count) — the highest-value fix; both methods are
   currently capped by it.
2. **A genuine multi-source-conflict regime** to test where the scan says the
   landscape should shine most (raising ridges to resolve cross-source conflicts).
3. **Full-scale + multi-seed CIs**, and a comparison against GoldenMatch's
   controller, not just the discrete loop.
