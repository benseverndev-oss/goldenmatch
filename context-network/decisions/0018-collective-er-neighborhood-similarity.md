# 0018 — Collective ER via neighborhood similarity

**Status:** accepted • **Shipped:** PR #1030 (2026-06-16), Phase 0+1

## Context
`run_graph_er`'s only cross-table signal was a flat, hand-set `evidence_weight`
boost (`_propagate_evidence`, `propagation_mode="additive"`): monotonic, so it can
only raise scores and over-merges; it ignores the canonical collective-ER signal,
*neighborhood similarity* (two records are more likely the same when their related
entities have themselves resolved together). There was no relational benchmark, so
any change was unfalsifiable. The ER-KG-Bench thesis (#1023) is that multi-field +
graph ER beats the single-threshold/LLM dedup KG/agent frameworks ship — collective
ER is where that moat is widest.

## Decision
Add `propagation_mode="relational"` (new `core/collective.py`): each candidate pair's
score = `(1-alpha)*attribute_sim + alpha*relational_sim`, where `relational_sim` is
neighbor-cluster overlap (Jaccard / Adamic-Adar) under the current clustering;
iterate to a fully-synchronous (Jacobi) fixpoint. Candidate set = attribute-blocked
pairs UNION capped co-neighbor pairs (so homonyms attributes miss can still resolve).
The existing `additive`/`multiplicative` flat-boost stays unchanged as a baseline +
back-compat. Gate everything on a relational fixture built so attributes alone are
weak; ship only on measured lift. Phased, each its own gate: P1 neighborhood
similarity (shipped), P2 negative evidence (deferred), P3 learned weights (deferred).

## Consequence
Measured pairwise F1 ~0.66 (attribute-only) -> ~0.87 (collective), stable across
seeds 7/8/9; the flat-boost `additive` mode is shown actively harmful on relational
data (~0.05, over-merge), which validates the critique. Default behavior is unchanged
(relational is opt-in; flat-boost output verified byte-identical). Spec/plan:
`docs/superpowers/specs/2026-06-16-collective-er-deepening-design.md`,
`docs/superpowers/plans/2026-06-16-collective-er-phase-0-1.md`. Bench:
`benchmarks/collective-er/`. P2/P3 open only after the prior gate passes on real
numbers. (0017 reserved for the in-flight dbt-parity ADR.)

---
**Classification:** decision/accepted • **Last updated:** 2026-06-16
