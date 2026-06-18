---
title: Vectorized survivorship resolution (Phase-2 columnar rewrite)
date: 2026-06-18
status: design (approved in brainstorming; pre-spec-review)
owner: Ben Severn
related:
  - docs/superpowers/specs/2026-06-17-survivorship-columnar-measure-first-design.md
  - docs/superpowers/reports/2026-06-18-survivorship-columnar-verdict.md
  - docs/superpowers/specs/2026-06-17-correlated-survivorship-and-conditional-golden-rules-design.md
  - docs/superpowers/specs/2026-06-17-allow-fill-anchor-strategy-design.md
---

# Vectorized survivorship resolution (Phase-2 columnar rewrite)

> The Phase-2 the measure-first verdict (GO) recommended. Stacks on the merged
> survivorship feature set (v1 + groups/conditional surfacing + allow_fill/anchor).

## 1. Problem

The measure-first bench (verdict report 2026-06-18) showed the survivorship
slow path is **19-20x the vectorized fast-path floor**, and **~98% of the slow
wall is the per-cluster Python loop** in `build_golden_records_batch`'s
survivorship branch:

```python
for cdf in s_sorted.partition_by("__cluster_id__", maintain_order=True):
    rec, _ = resolve_cluster(cdf, rules, order, cluster_id=int(cid))
```

`resolve_cluster` materializes per-column Python lists PER CLUSTER
(`col_arrays = {c: cluster_df[c].to_list()}`) and walks the resolution units in
Python. At 5M rows / 1.67M clusters this is ~250s vs the floor's 13s. The
non-survivorship path already vectorizes via
`_build_golden_records_polars_native` (`core/golden.py`): per-cluster winners
computed with `group_by("__cluster_id__").agg(...)`. This spec extends that
columnar machinery to survivorship.

## 2. Goals / non-goals

**Goals**
- A vectorized survivorship path that resolves field_groups (lock-step,
  incl. allow_fill), scalar field_rules, and the conditional fields, producing
  golden records **byte-identical to the slow path**, for `provenance=False`.
- Closes the measured gap: target the vectorized survivorship path within a
  low-single-digit multiple of the floor (vs 19-20x today), validated by
  re-running the bench.
- **Hybrid:** vectorize what is vectorizable (groups + non-conditional
  scalars); resolve only the conditional fields in a tiny per-cluster loop
  (reading the vectorized-resolved values). This handles MIXED configs (the
  measured workload).

**Non-goals**
- `provenance=True`: falls back to the existing slow path (lineage/audit is
  small-scale, not the bottleneck). No vectorized provenance reconstruction.
- Distributed/Sail (survivorship already refuses those).
- Removing or changing the slow path -- it remains the correctness oracle and
  the fallback. It is NOT deleted.
- Changing any survivorship semantics. Output must match the slow path exactly.

## 3. Design overview

A new `_build_golden_records_survivorship_native(multi_df, rules)` selected by
a new gate when survivorship is active, `provenance=False`, and the config's
levers are supported. It (a) sorts each cluster's rows once per group strategy,
(b) computes group + scalar values with `group_by`/agg, (c) runs a small
per-cluster loop for conditional fields only, (d) computes `__golden_confidence__`
to match the slow path exactly. The slow path is the parity oracle.

---

## Section 1 -- Gating

Add `_survivorship_native_eligible(rules, provenance) -> bool`: True when
`_survivorship_active(rules)` AND `provenance is False` AND every lever is
supported (groups with the four strategies, allow_fill, scalar field_rules,
conditional list-form rules, validate). Unsupported edges (e.g. a custom
plugin strategy, or `provenance=True`) -> False -> existing slow path.

In `build_golden_records_batch`, before the current `if _survivorship_active`
slow branch, add: `if _survivorship_native_eligible(rules, provenance): return
_build_golden_records_survivorship_native(multi_df, rules)`. The slow branch
stays as the fallback. The non-survivorship native gate is unchanged.

---

## Section 2 -- Group resolution (vectorized)

For each `GoldenGroupRule`, compute a per-cluster **strategy rank** (best row
first; stable ties -> lowest `__row_id__`, matching the slow path's
lowest-index winner). The rank key per strategy:
- `most_complete`: populated-count of the group's columns, descending.
  Populated-count = `sum(pl.col(c).is_not_null() for c in group.columns)`.
- `source_priority`: rank of `__source__` in `source_priority` (ascending;
  unknown sources last).
- `most_recent`: `date_column` descending, nulls last.
- `anchor`: `(anchor_col.is_not_null(), populated-count)` descending (anchor-
  present rows first, then most-complete; falls back to most_complete when no
  row has the anchor).

Sort the frame by `[__cluster_id__, <rank key oriented best-first>, __row_id__]`,
then per group column via `group_by("__cluster_id__", maintain_order=True).agg`:
- `allow_fill=False` -> `pl.col(c).first()` (the rank-0 winner's value, incl.
  nulls -> strict lock-step: every group column takes the same winner row).
- `allow_fill=True` -> `pl.col(c).drop_nulls().first()` (first non-null in rank
  order -> per-cell back-fill from the strategy-best other row).

Both forms reduce to per-column aggregates over the single per-cluster sort.
(Note: a separate sort per distinct group strategy; most configs have 1-2
groups.) The winner `__row_id__` per group is `pl.col("__row_id__").first()`
over the sorted frame -- not needed for the golden record (provenance=False)
but used for confidence ties (Section 5).

**Determinism (a real byte-identity hazard -- read carefully).** The slow
path's tie-break is the lowest POSITIONAL index after `multi_df.sort("__cluster_id__")`
with **no `maintain_order=True`** (`golden.py:888`). That positional order
equals `__row_id__`-ascending only because `multi_df` ARRIVES `__row_id__`-
ascending (filter preserves order) and the unstable sort happens to preserve
within-group order -- it is NOT guaranteed by polars. So the ORACLE itself is
potentially non-deterministic on tie-heavy clusters (same class as bug #870 /
`_stable_value_expr`). The vectorized path sorts with `__row_id__` as the
explicit final key, making it MORE deterministic than the oracle and defining
the canonical tie-break (lowest `__row_id__`).
**Consequence for the parity gate (Section 6):** run the oracle on a frame
pre-sorted by `[__cluster_id__, __row_id__]` (i.e. give the slow path a
deterministic within-cluster order) so the gate compares the vectorized path
against a deterministic oracle, not against undefined polars behavior. A
randomized tie-heavy parity test is the guard, but it MUST pin the oracle's
order first or it will flake on the oracle disagreeing with itself.

---

## Section 3 -- Scalar resolution (vectorized)

Non-conditional scalar fields (a plain `GoldenFieldRule`, or the default
strategy) extend the existing `_build_golden_records_polars_native` aggregate
approach. `most_complete`/`first_non_null` already exist there; add the other
strategies as per-cluster aggregates keyed off the same sort idea:
`most_recent` (value at max date), `source_priority` (value at best source
rank), `longest_value`, `majority_vote`, `unanimous_or_null`. Each is a
`group_by`/agg expression. Strategies that cannot be expressed as a pure
aggregate (e.g. a `custom:` plugin, or `confidence_majority` needing
pair_scores) make the config slow-path-ineligible (Section 1). (Only SCALAR
field_rules can hit a custom/`confidence_majority` strategy; the group strategy
set is a closed, validated `_GROUP_STRATEGIES`, so groups never force fallback
on strategy grounds.)

**All-agree short-circuit (confidence parity -- do not miss).** `merge_field`
returns value=first-non-null, confidence=`1.0` whenever the cluster has exactly
ONE distinct non-null value (the `nuniq<=1` path), BEFORE any strategy runs.
The existing native path already encodes this. Every native scalar strategy
MUST reproduce it: the strategy's own confidence (e.g. `most_recent` tie 0.7,
`source_priority` `1.0 - idx*0.1`, `majority_vote` `count/total`) applies ONLY
when there are >= 2 distinct non-null values; otherwise confidence is 1.0.
Miss this and confidence diverges on every all-agree cluster.

**Vectorized validation.** A scalar `field_rule` with `validate:` applies
`goldenflow_filter` to drop invalid candidates BEFORE the strategy
(`resolve.py`, scalar branch only -- groups never validate). Vectorize as a
per-column mask: run the validator (series-mode, already vectorized) over the
column, set invalid cells to null, THEN run the strategy agg on the masked
column. `dropped_invalid` is provenance-only (lands on `FieldProvenance`, never
affecting value/confidence), safe to ignore on the `provenance=False` path.

---

## Section 4 -- Conditional fields (small per-cluster loop)

Conditional fields (list-form `field_rules` with `when:`) are resolved AFTER
the vectorized units, in `build_resolution_order` toposort order. For each
conditional field, loop clusters and: evaluate the `when:` predicate
(`eval_predicate`) against that cluster's already-resolved values (read from
the vectorized result columns, NOT re-materialized from raw rows), pick the
clause's strategy, and apply it to the cluster's candidate values for that one
column. This loop materializes ONLY the conditional column(s) + the
`when:`-referenced resolved scalars -- a tiny fraction of the per-cluster work
the current loop does over ALL columns. **Reuse `select_conditional_strategy`
+ `eval_predicate` verbatim** (do NOT re-implement predicate eval); the
`_Miss`->False / null-operand miss semantics then come for free, identical to
the slow path. (If conditional fields dominate a
pathological config, the win shrinks, but the dominant materialization for
groups/scalars is already gone.)

**Toposort dependency:** a `when:` may reference a group member or a scalar;
those are resolved vectorized first, so their per-cluster values are available.
A `when:` referencing ANOTHER conditional field is resolved in toposort order
within this loop.

---

## Section 5 -- Confidence parity (the subtle surface)

`__golden_confidence__` must equal the slow path's value exactly. The slow path
(`resolve_cluster`): each UNIT contributes ONE confidence to a list, and
`__golden_confidence__ = mean(confidences)`.
- A **group** contributes one confidence = `(winner_populated + n_filled) /
  len(columns)`, times 0.7 on a winner tie. Three subtleties that must match
  `group_winner` exactly:
  - `winner_populated` = the rank-0 WINNER row's own non-null group-cell count,
    computed PRE-fill. Do NOT compute populated-count over the post-fill values
    dict (that would double-count filled cells, which already enter via
    `n_filled`).
  - `n_filled` (allow_fill only) = group cells where the winner is null AND a
    non-null donor exists elsewhere in the cluster. A winner-null cell with NO
    donor stays null and is NOT counted (`group_winner` only sets `filled[c]`
    when a donor is found).
  - Tie = more than one row shares the top rank key -- but `tie` is **only ever
    True for `most_complete` and `anchor`** (`_ranking` hard-codes `tie=False`
    for `source_priority` and `most_recent`). So source_priority/most_recent
    groups NEVER get the 0.7 factor.
  All are vectorizable per cluster (counts + a tie indicator from the sort).
- A **scalar** contributes the strategy's own confidence (per `merge_field`).
- The mean is over (n_groups + n_scalars + n_conditionals) units.

This is the trickiest parity surface (tie 0.7, allow_fill fill-count, the
one-per-group-mean convention). The parity gate (Section 6) MUST assert
`__golden_confidence__` byte-equality, and a dedicated confidence test covers
tie + allow_fill + mixed-unit clusters.

---

## Section 6 -- Parity gate (LOAD-BEARING)

The vectorized golden DataFrame must be **byte-identical** to the slow path's
on the same `multi_df`, for `provenance=False`. A property-style gate compares
`_build_golden_records_survivorship_native(df, rules)` against the slow path
(`build_golden_records_batch` slow branch) for:
- Each group strategy (most_complete/source_priority/most_recent/anchor),
  allow_fill on/off, including tie-heavy and all-null clusters.
- Conditional + validated configs (when:/validate), mixed with groups.
- Randomized configs + frames (seeded) -- the real Frankenstein/tie catcher,
  as in prior survivorship gates.
- Column values AND `__golden_confidence__` AND row count (one record per
  cluster).
Any mismatch fails. This gate IS the correctness guarantee; the slow path is
the oracle.

---

## Section 7 -- Validation + Stage 0

- **Stage 0 (de-risk, optional but recommended):** a `py-spy` split over one 5M
  slow run, confirming per-cluster materialization (+ group_winner) is the
  dominant recoverable cost (the verdict assumed this; py-spy pins it). If a
  surprise (e.g. conditional eval dominates), revisit the hybrid split before
  building.
- **End validation:** re-run `bench-survivorship-columnar.yml` with the
  vectorized path enabled; the verdict report's table gains a "native" column.
  Success = the vectorized survivorship wall is within a low-single-digit
  multiple of the floor (vs 19-20x), with no RSS blow-up (the corrected RSS
  gate). If it does NOT close the gap materially, that is itself a finding
  (revert / keep slow) -- measure-first to the end.

## Section 8 -- Testing + module layout

- Parity gate (Section 6) in `tests/survivorship/test_native_parity.py`.
- Per-strategy + allow_fill + conditional + confidence + fallback
  (`provenance=True` -> slow) unit tests.
- New code in `core/golden.py` (`_survivorship_native_eligible`,
  `_build_golden_records_survivorship_native`) or a new
  `core/survivorship/native.py` if it grows past ~200 lines (the resolver
  package already owns the slow path; a sibling `native.py` keeps `golden.py`
  from bloating -- decide at implementation time by size).
- Docs sweep: note the vectorized path + the provenance=True fallback in the
  tuning/config docs.

## 9 -- Open questions for spec review

- **Confidence exactness vs the existing native approximation.** The current
  `_build_golden_records_polars_native` deliberately APPROXIMATES confidence
  (0.7 when >1 non-null). The survivorship-native path needs EXACT slow-path
  confidence. Confirm we compute exact confidence in the new path (not reuse
  the approximation), and that the parity gate covers `__golden_confidence__`.
- **Scalar strategy coverage.** Which scalar strategies are expressible as pure
  aggregates (so the config is native-eligible) vs force slow-path? Draft list
  in Section 3; confirm `most_recent`/`source_priority`/`longest_value` are
  exactly aggregate-able to match `merge_field`, and that `confidence_majority`
  / `custom:` correctly force fallback.
- **Per-strategy sort cost.** Each distinct group strategy needs its own
  per-cluster sort. Confirm this stays well under the slow-path cost (it
  should -- 1-2 vectorized sorts vs 1.67M Python iterations) and consider
  sharing the sort when groups share a strategy.
- **Stage 0 py-spy:** required before building, or proceed on the strong
  reasoning + the parity/bench gates? Leaning recommended-but-not-blocking.
