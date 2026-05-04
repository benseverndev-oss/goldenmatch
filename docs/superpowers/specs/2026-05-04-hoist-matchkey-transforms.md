# Hoist matchkey transforms out of the per-block scoring loop

**Date:** 2026-05-04
**Status:** Design approved, awaiting implementation plan
**Parent:** `2026-05-02-performance-audit-checklist.md` (replaces "vectorize pattern_consistency" as the actual #1 runtime win after profiling)

## Goal

Eliminate redundant Polars `frame.select(...)` calls during dedupe scoring by precomputing each unique `(field, transforms)` pair once on the parent DataFrame, then having per-block scoring read the precomputed column instead of re-transforming.

## Background

cProfile of a representative 10k-row dedupe workload (`.profile_tmp/profile_dedupe.py`, 2026-05-04):

```
Total wall:                          11,440 ms
scorer._get_transformed_values        8,970 ms  (78%)  ← 7028 calls
  └─ frame.select (Polars)            8,260 ms  (72%)  ← per-block, per-field selects
score_blocks_parallel                 4,680 ms  (41%)  ← already threaded
series.map_elements                   2,110 ms  (18%)  ← only 33 calls
blocker._build_static_blocks          1,790 ms  (16%)  ← single call
```

**Why `_get_transformed_values` dominates:** `find_fuzzy_matches(block_df, mk)` runs once per block. Inside, for each matchkey field, it calls `_get_transformed_values(block_df, field)`, which invokes `block_df.select(_try_native_chain(...))`. With ~2350 blocks × 3 fields = ~7050 calls. Each `Polars.select` round-trips through `deprecation`/`opt_flags` wrappers, even though every block scores the same value through the same transform chain.

Transforms are pure (`lowercase`, `strip`, `soundex`, `digits_only`, etc.). The same value transforms to the same output every call. The redundancy is structural, not semantic.

The audit originally ranked "vectorize `pattern_consistency` profiler" as the biggest runtime win. Measurement disproved that (1.1–1.4x speedup, end-to-end flat). This spec is the *actually-measured* #1 win for goldenmatch dedupe.

## Non-goals

- Refactoring the standardize step (`_NATIVE_STANDARDIZERS`) — separate concept (global cleanup vs per-matchkey scoring prep).
- Optimizing `_build_static_blocks` (1.79s, single call) — separate item.
- Cross-process or cross-call caching — adds invalidation surface; not worth it for a single-call op.
- Changing what gets transformed or how — pure plumbing change, semantics unchanged.

## Design

### 1. New helper: `precompute_matchkey_transforms`

In `packages/python/goldenmatch/goldenmatch/core/matchkey.py` (alongside the existing `_try_native_chain`):

```python
import hashlib


def _xform_sig(field: MatchkeyField) -> str:
    """Stable, process-independent signature for a (field, transforms) pair.

    Uses blake2b rather than Python's salted hash() so the resulting column
    name is deterministic across processes — makes debugging dumps diffable
    and avoids spooky cross-run differences in error messages.
    """
    digest = hashlib.blake2b(
        repr(field.transforms).encode(), digest_size=8
    ).hexdigest()
    return f"__xform_{field.field}_{digest}__"


def precompute_matchkey_transforms(
    df: pl.DataFrame, matchkeys: list[MatchkeyConfig]
) -> pl.DataFrame:
    """Add one __xform_<sig>__ column per unique (field, transforms) signature.

    Same field+transforms across multiple matchkeys reuses one column — dedup
    is automatic via the signature. Native chains use _try_native_chain (Rust);
    non-native chains fall back to Python per-row apply_transforms once.

    Skips fields whose scorer is `record_embedding` — those use multi-column
    `field.columns` (not a single `field.field`) and have a separate scoring
    path (`_record_embedding_score_matrix`) that does NOT call
    `_get_transformed_values`. Including them here would (a) collide on the
    pseudo-field name "__record__" and (b) crash on `df[field.field]` because
    the named single column doesn't exist for that scorer.

    Skips fields whose `transforms` list is empty — there's nothing to
    precompute, and `_get_transformed_values` falls through to the legacy
    path which is already a single `to_list()` call (no Polars overhead to
    eliminate).

    Returns the augmented DataFrame. Original columns are untouched.
    """
    seen: set[str] = set()
    new_cols: list[pl.Series] = []
    for mk in matchkeys:
        for field in mk.fields:
            if field.scorer == "record_embedding":
                continue
            if not field.transforms:
                continue
            sig = _xform_sig(field)
            if sig in seen or sig in df.columns:
                continue
            seen.add(sig)

            native_expr = _try_native_chain(field.field, field.transforms)
            if native_expr is not None:
                col = df.select(native_expr.alias(sig))[sig]
            else:
                values = df[field.field].to_list()
                col = pl.Series(
                    sig,
                    [apply_transforms(v, field.transforms) if v is not None else None
                     for v in values],
                )
            new_cols.append(col)

    if not new_cols:
        return df
    return df.with_columns(new_cols)
```

### 2. Pipeline wiring

In `packages/python/goldenmatch/goldenmatch/core/pipeline.py`, insert one call to `precompute_matchkey_transforms(df, config.get_matchkeys())` **immediately before the `build_blocks(...)` call** in both:
- `_run_dedupe_pipeline` (around line 407)
- `_run_match_pipeline` (around line 802-836; the match pipeline calls `build_blocks` for the same reason and benefits identically)

This placement runs *after* all upstream column-mutating steps:
1. `standardize_columns(...)` — global cleanup
2. `compute_matchkeys(...)` — adds `__mk_*__` columns
3. Optional domain extraction / LLM extraction (lines 355-368) — may add `__brand__`, `__model__`, etc. that matchkey transforms reference

The augmented df flows through blocking and scoring naturally — every block-construction strategy in `core/blocker.py` (static, sorted-neighborhood, ANN, canopy, multi-pass, learned) builds blocks via `df.filter`, `df.slice`, `df[member_list]`, or `lf.with_columns(...).collect()`, all of which preserve arbitrary added columns.

Eager placement (rather than lazy inside `find_fuzzy_matches`) is the chosen design because:
- Pipeline.py is the natural layer for "prepare data once for all blocks."
- Keeps `find_fuzzy_matches` a pure scoring function with no side effects.
- Makes the cost visible in the pipeline trace, not hidden in scoring.

### 3. Lookup in scorer

In `packages/python/goldenmatch/goldenmatch/core/scorer.py`, modify `_get_transformed_values`:

```python
def _get_transformed_values(block_df: pl.DataFrame, field: MatchkeyField) -> list:
    """Get transformed values for a field as a list.

    Fast path: read precomputed __xform_*__ column (eager precompute via
    precompute_matchkey_transforms in pipeline). Fallback path preserves
    backward compatibility for callers that bypass the pipeline (DataFrame
    entry points, tests calling find_fuzzy_matches directly).
    """
    from goldenmatch.core.matchkey import _xform_sig, _try_native_chain

    sig = _xform_sig(field)
    if sig in block_df.columns:
        return block_df[sig].to_list()

    # Legacy path — preserved verbatim
    col = field.field
    native_expr = _try_native_chain(col, field.transforms)
    if native_expr is not None:
        result_df = block_df.select(native_expr.alias("__tmp__"))
        return result_df["__tmp__"].to_list()
    values = block_df[col].to_list()
    return [apply_transforms(v, field.transforms) if v is not None else None for v in values]
```

### 4. Why this is safe

- Transforms are deterministic functions of value: caching is correct by construction.
- Native chains use the same `_try_native_chain` we already trust.
- The `__xform_*__` columns use the project's existing internal-prefix convention (double underscore, per `goldenmatch/CLAUDE.md` "Internal columns prefixed with `__`").
- Memory cost: one Series per unique `(field, transforms)` pair. 10k rows × 3 fields ≈ 30k strings ≈ ~3MB. Negligible.
- Backward compatibility: the lookup path has a fallback so `DataFrame entry points` (`dedupe_df`, `match_df`) and tests that call `find_fuzzy_matches` directly continue to work — just at the old speed unless they precompute first.

## Tests

- **`test_precompute_matchkey_transforms_dedups_signatures`** — same field+transforms across two matchkeys produces one column.
- **`test_precompute_matchkey_transforms_distinct_transforms_same_field`** — same field with two different transform chains produces two distinct columns (the deduplication only fires on identical signatures).
- **`test_precompute_matchkey_transforms_native_chain_path`** — verifies fast path uses `_try_native_chain` output.
- **`test_precompute_matchkey_transforms_python_fallback_path`** — verifies non-native chain (e.g., a hypothetical custom transform) falls through to per-row Python.
- **`test_precompute_matchkey_transforms_skips_record_embedding`** — a matchkey with a `record_embedding` field does not raise, does not add a `__record__` column, and the rest of the matchkey's fields still get precomputed.
- **`test_precompute_matchkey_transforms_skips_empty_transforms`** — a field with `transforms=[]` is not given a `__xform_*__` column (no-op precompute).
- **`test_get_transformed_values_uses_precomputed_column_when_present`** — block_df with `__xform_*__` returns those values, doesn't re-select.
- **`test_get_transformed_values_falls_back_when_column_absent`** — block_df without `__xform_*__` produces identical output to the legacy path (regression pin).
- **`test_find_fuzzy_matches_identical_results_with_and_without_precompute`** — full end-to-end equivalence on a small synthetic dataset.
- **`test_xform_sig_is_deterministic_across_processes`** — call `_xform_sig` twice on equivalent fields, assert identical output. Pins the blake2b choice against accidental reverts to `hash()`.
- **Reuse the existing 1319-test goldenmatch suite untouched** — drop-in compatibility check.

## Acceptance criteria

**Verified locally before merge:**
- All existing tests pass (1319-test suite, modulo the known pre-fold ignore list).
- New tests above pass.
- `.profile_tmp/profile_dedupe.py` re-run shows:
  - `_get_transformed_values` cumulative time drops from ~8.97s to **<1s** on the 10k synthetic workload.
  - End-to-end wall clock improves by **≥2x** (≤5.5s vs current 11.4s baseline).

**Acceptance is conditional on the synthetic showing ≥3x on the hot function.** If the win is smaller than expected on real-shaped data, ship anyway — the per-block `select()` pattern is structurally wrong and the cleanup is worth it independent of magnitude.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| `_xform_sig` collisions (two different `(field, transforms)` hash to the same column name) | Very low (blake2b 64-bit digest over `repr(transforms)`) | Resolved in the design by using `hashlib.blake2b` (process-independent). Collision probability for <1000 unique signatures is ~3e-14. |
| `with_columns` on a wide DataFrame (200+ cols) is slow | Low — only at pipeline start, single call | Acceptable; the 7000 select() calls being eliminated dwarf this. |
| A custom matchkey transform mutates state across calls | Low (transforms are documented as pure) | Existing test surface catches this; no new risk introduced. |
| `dedupe_df` (DataFrame entry point) skips precompute and silently runs slow | Medium | Add `precompute_matchkey_transforms` call inside `_run_dedupe_pipeline` so all entry points using that pipeline benefit; document the legacy fallback path. |

## Working agreement

- Implementation goes through writing-plans next; this spec is the input.
- Capture before/after wall clock + `_get_transformed_values` cumulative time in the PR description, both for the synthetic 10k workload and for one realistic shape if available.
- Update parent checklist (`2026-05-02-performance-audit-checklist.md`) with the measured result, mirroring the pattern_consistency entry: hypothesis → measured reality → decision.
