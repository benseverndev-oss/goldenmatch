# 0001 — Gate reframe: engine portability, not one-box RSS

**Status:** accepted (2026-06-03) • **Supersedes:** the Phase-2 "RSS −30% @25M" gate for this track

## Context
The Arrow-native cutover was originally gated on one-box peak-RSS reduction. But the
destination is a distributed engine (DataFusion single-box out-of-core; Sail distributed)
which scales *out* — one box always has a ceiling, so RSS measured the wrong axis.

## Decision
Retire the one-box RSS gate for the Arrow-native track. Replace with:
1. **Engine portability** — every stage is a relational plan an engine can own (the one
   non-relational stage, Union-Find cluster build, routes to label-prop, not DataFusion).
2. **Out-of-core / distributed throughput** — wall holds out-of-core (DataFusion spill)
   and scales across nodes (Sail).

The compact columnar representation isn't wasted: at distributed scale it reappears as
shuffle/spill efficiency (packed Arrow frames beat dict-of-dicts).

## Consequences
- The DataFusion spine ([../architecture/datafusion-spine.md](../architecture/datafusion-spine.md))
  is the concrete step-2 work (step 1, id_prep-as-group-by, was proven in #696).
- The Union-Find cluster build is the named non-relational holdout — by nature, not a gap.
- This reframe is what makes the Stage E honest-null result
  ([0003-stage-e-spill-honest-null.md](0003-stage-e-spill-honest-null.md)) a *non-failure*:
  the value claimed is portability, not one-box survival.

**Source:** roadmap doc § "Gate reframe: engine portability".

---
**Classification:** decision/accepted • **Last updated:** 2026-06-03
