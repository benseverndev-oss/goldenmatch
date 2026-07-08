# Fused Arrow-native golden-record kernel — design

**Date:** 2026-07-08
**Status:** Design (approved), pre-implementation
**Package:** `goldenmatch` (Python + `goldenmatch-native` Rust extension)
**Related:** the fused-match arc (`match_fused` / `match_fused_fs`, PRs #1590/#1591/#1599/#1600),
`docs/design/2026-07-08-fused-arrow-native-match-kernel.md`, correlated survivorship #1047,
per-cluster overrides #429.

## 1. Problem

The fused Arrow-native match stage (`match_fused`, `match_fused_fs`) delivers a measured
**~2x lower peak RSS = 2x single-box capacity** at byte-identical output, by keeping
block/score/dedup/cluster intermediates as Rust `Vec`s behind one FFI crossing — no
intermediate Polars frame or Python pairs-list is materialized.

But it stops at **clusters only**: `run_match_fused_arrow` and friends return a two-column
`(__row_id__, __cluster_id__)` Arrow table. The pipeline's actual deliverable is **golden
records**, produced separately by `core/golden.py::build_golden_records_batch` on a Polars
`multi_df` (the cluster assignments joined back to every source column, one row per clustered
record). That `multi_df` — all user columns for all non-singleton clustered rows — plus the
per-cluster Python dicts the slow path builds, is a large RSS allocation. So even where fused
match wins on RSS, a user who wants golden records drops back to the classic Polars path and
**pays the RSS back**. The capacity win does not reach the output people consume.

**Goal:** extend the fused RSS win through to golden-record production, byte-identical, for the
common config surface — so a covered dedupe -> golden workload keeps its ~2x capacity headroom.

## 2. Scope

### In scope (v1)

- A standalone Arrow-native golden-record kernel: **cluster map + decision columns -> golden
  records**, holding intermediates as Rust `Vec`s, avoiding the wide `multi_df` and per-cluster
  Python dicts.
- **Coverage: everything Rust-portable.** Every survivorship strategy and config feature EXCEPT
  the three unavoidable Python callbacks:
  - `most_complete`, `first_non_null`, `longest_value`, `majority_vote`, `unanimous_or_null`,
    `most_recent`, `source_priority`, `confidence_majority`
  - `field_groups` (correlated survivorship, incl. `allow_fill` / `anchor`)
  - conditional `field_rules` (list form with `when:` predicates)
  - `cluster_overrides` (#429)
  - `quality_weighting` (per-cell quality scores)
- **Output surface: golden frame + provenance.** All user columns at their **native dtype** +
  `__cluster_id__` + `__golden_confidence__`; plus, when `provenance=True`, a per-field
  `source_row_id` provenance frame.
- Byte-parity tests vs `build_golden_records_batch`, a memory-capped RSS bench proving the
  capacity win, and a Febrl3 dogfood.

### Declined -> classic Polars path (loud, never silent)

- `validate_with` (GoldenFlow validator transforms — call back into Python)
- `custom:<name>` (plugin strategies — call back into Python)
- `use_llm_for_ambiguous` (LLM — calls out)
- any conditional `when:` predicate that does not lower to the kernel IR (see 6.3)

### Explicit non-goals (deferred to later PRs)

- Wiring into `pipeline.py` (a real `dedupe()` still uses the classic path).
- Controller auto-routing (RSS-bound profile -> fused).
- The distributed/Sail backends (correlated survivorship already refuses there today).

## 3. Approach (chosen: A — separate composable kernel)

A new Rust pyfunction `golden_fused` (own file `golden.rs`), distinct from `match_fused`, that
consumes a cluster map `(row_id, cluster_id)` plus the decision columns as borrowed Arrow, and a
new Python module `core/golden_fused.py` that assembles inputs, calls the kernel, and materializes
output. Layout mirrors `core/fused_match.py`.

Alternatives rejected:

- **B — fully fused single call** (block -> score -> cluster -> golden in one FFI). Max
  theoretical RSS (clusters never surface), but couples golden coverage to match coverage
  (intersection only), can't run on classic clusters, is far harder to test (no known-cluster
  injection), and the only RSS it saves over A is the 2-column cluster map — negligible next to
  the `multi_df` A already removes. High risk, low marginal gain.
- **C — Arrow-seam over the existing Polars-vectorized `core/survivorship/native.py`.** Lowest
  parity risk (reuse vetted Polars expressions), but the RSS hog *is* the Polars `multi_df` +
  expression intermediates, so it almost certainly doesn't move peak RSS — it fails the capacity
  goal, and contradicts the "everything Rust-portable" scope.

## 4. The two load-bearing decisions

### 4.1 The kernel returns indices, never values

For each cluster `c` and output column `col`, the kernel emits:

- `winner_idx[c][col]: i64` — the 0-based **source-row position** whose value survives, or `-1`
  for null.
- `field_conf[c][col]: f64` — the confidence for that field.

Python materializes output with one `Series.gather(winner_idx)` per column on the **original,
typed** Series, mapping `-1` -> null. This buys, for free:

- **Native dtypes + byte-identical values** — Python gathers from the original typed data; no
  Utf8 coercion, no re-serialization, no dtype round-trip.
- **Provenance** — `source_row_id` for a field IS its winner index mapped through `__row_id__`.
- **RSS win** — the kernel holds borrowed Arrow + integer index `Vec`s; output is `n_clusters`
  rows, never the wide `multi_df`. The `multi_df` and per-cluster dicts never exist.

`__golden_confidence__[c]` = the mean of `field_conf[c][*]`, matching the reference aggregation
in `build_golden_records_batch` (exact aggregation to be confirmed against source in the plan).

The one non-row output — `unanimous_or_null` emitting `None` on disagreement — is the `-1`
sentinel. Because every covered strategy's winning value is *some source row's value*
(majority/confidence winners are values present in a row; the representative row is the
first-occurrence of the winning code, which also matches the reference `source_index` for
provenance), the "winner index per (cluster, column)" abstraction covers the whole surface.

### 4.2 Python precomputes comparable keys; Rust never sees raw values

To stay byte-identical without porting Arrow's type system or Python `==` semantics into Rust,
Python passes, per decision column, only what the column's resolved strategy needs:

- `text: Utf8` — the `str(v)` form, for the length strategies (`most_complete`, `longest_value`),
  matching the reference `str(v)` length computation.
- `code: i64` — a **factorization** of the raw values (equal raw value -> equal code,
  **first-occurrence order preserved**, `-1` = null), for the equality/grouping strategies
  (`majority_vote`, `unanimous_or_null`, `confidence_majority`). Because the factorization respects
  Python `==`, grouping on codes is byte-identical to the reference's `Counter`/`dict` grouping,
  and first-occurrence order makes the tie-breaks (winner = first occurrence) match. This removes
  the mixed-type-column trap (int `1` vs float `1.0`, which are `==` in Python but differ as
  strings) that a naive Utf8 pass would hit.
- `date: i64` — only for `most_recent` columns; parsed in Python via the reference's own date path.
- `source_code: i64` — only when `source_priority` is in play; factorized `__source__`.
- `qweight: f64` per (row, col) — only when `quality_weighting` is active.

## 5. Data flow

```
run_golden_fused_arrow(columns, cluster_map, rules, quality_scores?, pair_scores?, provenance?)
  |
  |-- golden_fused_ready(rules)?  no -> return None  (caller uses build_golden_records_batch)
  |
  |-- Python: resolve effective strategy per (cluster, col) incl. cluster_overrides
  |-- Python: build resolution order (reuse conditions.py.build_resolution_order)
  |-- Python: lower each conditional when: predicate to IR (reuse conditions.py parse/validate)
  |-- Python: per decision column, build the minimal keys (text / code / date / source_code / qweight)
  |-- Python: flatten intra-cluster pair scores to per-cluster arrays (confidence_majority)
  |
  |-- FFI: golden_fused(cluster_map, per-col keys, group specs, predicate IR,
  |                     strategy codes, weights, pair-score arrays) -> (winner_idx, field_conf)
  |
  |-- Python: materialize output frame (gather per col at native dtype) + __cluster_id__
  |           + __golden_confidence__; provenance frame if requested
  '-- return pl.DataFrame  (+ provenance)
```

Singletons / oversized clusters are excluded exactly as `_multi_df_from_frames` does today
(`size > 1 & ~oversized`).

## 6. Feature mapping onto index-return + codes

### 6.1 Scalar strategies

Each reduces to "pick a source-row index + emit a confidence," mirroring `merge_field`
case-for-case (`core/golden.py`). Tie-breaks and confidence values are simple deterministic
arithmetic (e.g. `most_complete`: unique longest -> 1.0; length-tie -> highest `qweight`, else
first in order; `source_priority`: `max(0.1, 1.0 - idx*0.1)`), portable exactly. The universal
pre-dispatch short-circuit (drop nulls; all non-null identical -> that value at confidence 1.0)
is applied in the kernel first, matching `golden.py`.

### 6.2 field_groups (correlated)

The kernel ranks rows once per group (populated-count from the group columns' null masks /
`source_code` / `date` / anchor-presence, per the group strategy: `most_complete`,
`source_priority`, `most_recent`, `anchor`) and pins **one** winner index across all group
columns — or, with `allow_fill`, per-column back-fill indices from the next-best ranked row that
has the column. The group contributes **one** confidence to the cluster mean
(`base = (winner_populated + n_filled) / len(columns)`; `x0.7` on tie), matching
`core/survivorship/groups.py::group_winner` and `resolve.py`.

### 6.3 Conditional field_rules (predicate IR)

Python owns parsing: reuse `core/survivorship/conditions.py` to parse + validate (its existing
AST allowlist) + `build_resolution_order` (topological sort so a `when:` that references another
field/group resolves after it). Each `when:` predicate is **lowered to a small RPN / typed IR**:

- boolean ops (`and`/`or`/`not`), membership (`in`/`not in`), equality (`==`/`!=`) evaluated in
  the referenced column's **code space** (each literal pre-resolved to that column's code, or a
  reserved "absent" sentinel when the literal is not a present value);
- numeric comparisons (`<`/`<=`/`>`/`>=`) evaluated in a numeric value lane.

The kernel resolves units in the given order; for a conditional column it evaluates the IR against
already-resolved winner codes, picks the first passing clause's strategy, applies it, else the
when-less default clause. **Any predicate construct that does not lower -> the gate declines the
whole config** (loud fall-through, not silent). Miss semantics (unknown name / uncomparable ->
False arm) match `conditions.py::eval_predicate`.

### 6.4 confidence_majority

Python flattens intra-cluster pair scores to per-cluster arrays. The kernel sums edge scores per
value-code over edges where both endpoints hold that code, max wins,
`winner_edge_sum / total_edge_sum`; empty/absent -> falls back to `majority_vote`. Pure arithmetic,
byte-identical.

### 6.5 cluster_overrides (#429)

Strategy can vary per cluster, so Python passes a per-(cluster, col) **strategy-code** array; the
kernel dispatches on it. Just a small enum code — no structural change.

### 6.6 quality weights

Thread into the tie-breaks (`most_complete`/`longest` length-tie -> highest `qweight`; weighted
`majority_vote` / `first_non_null`) exactly as `merge_field` does with `quality_weights`.

## 7. The gate

`golden_fused_ready(rules) -> bool` returns True iff:

- every effective strategy (default + `field_rules` + `field_groups` + per-cluster overrides) is in
  the covered set; and
- no `validate_with`, no `custom:*`, no `use_llm_for_ambiguous`; and
- every conditional `when:` predicate lowers to the IR (6.3).

Otherwise False -> caller falls through to `build_golden_records_batch`. Same decline-loudly
posture as `match_fused_ready`.

## 8. Output materialization (Python)

From `winner_idx[c][col]` + `field_conf`:

- one `Series.gather(idx)` per column on the original typed Series with `-1` -> null (native dtype
  preserved);
- `__cluster_id__` column;
- `__golden_confidence__` = mean of `field_conf` (matching the reference aggregation);
- provenance mode adds a per-(cluster, col) `source_row_id` frame = `__row_id__[winner_idx]`.

## 9. Testing (byte-parity is the whole game)

- **Rust unit tests** (`golden.rs`): each strategy's index+confidence; groups incl.
  `allow_fill` / `anchor`; IR evaluation; `confidence_majority`; null/sentinel handling.
- **Python parity matrix** (`tests/test_golden_fused.py`): for each strategy, groups, conditionals,
  `confidence_majority`, quality-weights, `cluster_overrides`, and provenance — run
  `run_golden_fused_arrow` and `build_golden_records_batch` on identical clusters + frame; assert
  **frame equality (values + dtypes + confidence)** and **provenance equality**. Includes
  **mixed-type-column fixtures** (int `1` vs float `1.0`, numeric-as-string, etc.) to pin the
  factorization edge (4.2).
- **Gate tests**: covered -> True; each declined arm (validator / plugin / LLM / unlowerable
  predicate) -> False.
- **Memcap RSS bench** (`scripts/bench_golden_fused_memcap.py` + workflow): under a cgroup cap,
  peak RSS of `golden_fused` vs `build_golden_records_batch` on the same clusters -> prove the
  capacity win. The honest headline metric is RSS/capacity, per the fused-match verdict (wall is
  expected to be a wash).
- **Dogfood** on Febrl3.

## 10. Files

New except `lib.rs` (register) and a docs sweep:

- `packages/rust/extensions/native/src/golden.rs`
- `packages/rust/extensions/native/src/lib.rs` — register `golden::golden_fused`
- `packages/python/goldenmatch/goldenmatch/core/golden_fused.py`
- `packages/python/goldenmatch/tests/test_golden_fused.py`
- `packages/python/goldenmatch/scripts/bench_golden_fused_memcap.py`
- `.github/workflows/bench-golden-fused-memcap.yml`
- doc-surfaces sweep at the end of the rollout

## 11. Risks

- **Parity of intricate tie-breaks and confidence floats.** Mitigation: mirror `merge_field` /
  `group_winner` case-for-case; the parity matrix (incl. mixed-type fixtures) is the gate.
- **Predicate IR completeness.** Mitigation: cover the lowerable subset, decline the rest loudly;
  parse/validate stays in the vetted `conditions.py`.
- **Confirming the exact `__golden_confidence__` aggregation** and majority/confidence
  `source_index` semantics against source before coding — pinned in the implementation plan.
- **Native kernel republish discipline** (adding new symbols): the published wheel must carry
  `golden_fused` or every `pip install goldenmatch[native]` env silently hits the classic path;
  bump `pyproject.toml` + `Cargo.toml` in lockstep and verify the symbol is in the published wheel
  (per the #688 wheel-skew lesson).
