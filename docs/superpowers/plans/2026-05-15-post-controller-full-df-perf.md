# Post-controller full-df perf — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut `gm.dedupe_df(df)` zero-config wall by ≥40% at 500K on the default polars-direct backend by killing the two cross-stage Python hotspots the bench identified (462K `LazyFrame.collect` calls and 55M `builtins.max` calls in the cluster split path).

**Architecture:** Two sequential PRs. **Attack A** rewrites `BlockResult` to carry positional indices instead of a per-block `LazyFrame`, eliminating the `lazy() → collect()` round-trip in `apply_learned_blocks` and the per-block collect in `_score_one_block`. **Attack B** vectorizes `_build_mst` and `compute_cluster_confidence` in NumPy to remove the 55M Python `max()`/`min()` calls. Each PR has its own measurement gate. Net target: 500K median 488s → ≤150s.

**Tech Stack:** Python 3.12, Polars, NumPy, dataclasses, Hypothesis (property tests), cProfile + the `scripts/bench_1m_zero_config.py` harness for measurement gates.

**Spec:** `docs/superpowers/specs/2026-05-15-post-controller-full-df-perf-design.md` — read this first; the plan refers to it by section.

---

## Pre-flight checklist

Before starting any task:

- [ ] Working in a clean dedicated branch. From `main`: `git switch -c perf/post-controller-full-df`.
- [ ] Editable install active: `C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -c "import goldenmatch._api; print(goldenmatch._api.__file__)"` shows `D:\show_case\goldenmatch\packages\python\goldenmatch\...`, not `site-packages`.
- [ ] Baseline test suite green from package dir: `cd packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q --timeout=120 --ignore=tests/test_db.py --ignore=tests/test_reconcile.py --ignore=tests/test_mcp_and_watch.py --ignore=tests/test_embedder.py --ignore=tests/test_llm_boost.py --ignore=tests/benchmarks` returns the documented 1572+ passing.
- [ ] 500K fixture present at `.profile_tmp/scale_fixtures/synthetic_500000_dupe15.csv` (it is, per the bench that produced this spec).
- [ ] Baseline bench JSON present at `.profile_tmp/bench_500k_zero_config.json` (committed; do not regenerate as part of pre-flight).

---

## Phase 0 — Verify spec's multi-pass single-parent-df assumption

Per spec §Attack A "Risk and mitigation" bullet 3 and reviewer advisory #1: confirm that all `build_blocks` callers operate against a single materialized parent DataFrame before committing to the `BlockResult.member_positions` contract. If multi-pass blocking builds blocks against differently-transformed frames, the positional contract needs refinement.

### Task 0.1: Audit `build_blocks` call sites

**Files:**
- Read-only: `packages/python/goldenmatch/goldenmatch/core/pipeline.py`, `goldenmatch/tui/engine.py`, `goldenmatch/core/chunked.py`, `goldenmatch/core/blocker.py`, `goldenmatch/core/learned_blocking.py`

- [ ] **Step 1: Grep all `build_blocks(` / `_build_learned_blocks(` / `_build_canopy_blocks(` / `_build_ann_pair_blocks(` call sites.**

```bash
grep -rn "build_blocks\|_build_learned_blocks\|_build_canopy_blocks\|_build_ann_pair_blocks" packages/python/goldenmatch/goldenmatch/ --include="*.py"
```

- [ ] **Step 2: For each call site, confirm:** (a) the LazyFrame passed in derives from the same materialized DataFrame that flows into `score_blocks_parallel` afterward, and (b) the `__row_id__` column is stable across both (no row drops or reorders between block-build and scoring).

- [ ] **Step 3: Record findings inline in this plan** by adding a short note under Phase 0 below this task. Two acceptable outcomes:
   - **All call sites OK:** "Single parent df confirmed for static / adaptive / learned / canopy / ann blocking — positional contract is safe." Proceed to Phase 1.
   - **Mismatch found:** "Path X passes a transformed frame; needs Y change." STOP and add a Phase 0.2 task to surface to the user.

- [ ] **Step 4: No commit** — this is read-only verification.

**Findings (fill in during execution):**
> _Implementer: write findings here, one paragraph._

---

## Phase 1 — Attack A: BlockResult positional contract

Per spec §Attack A. Replace `BlockResult.df: LazyFrame` with a positional contract that stores row indices and materializes on demand at the single scoring consumer.

### Task 1.1: Add `BlockResult.member_positions` + `materialize()` helper

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/blocker.py:87-96` (the `BlockResult` dataclass)
- Test: `packages/python/goldenmatch/tests/test_blocking.py` (new test function appended)

- [ ] **Step 1: Write the failing test.**

Append to `packages/python/goldenmatch/tests/test_blocking.py`:

```python
def test_block_result_materialize_uses_positions():
    """BlockResult with member_positions materializes via positional slice,
    not via LazyFrame.collect — the load-bearing invariant for Attack A
    (spec docs/superpowers/specs/2026-05-15-post-controller-full-df-perf-design.md)."""
    import polars as pl
    from goldenmatch.core.blocker import BlockResult

    df = pl.DataFrame({
        "__row_id__": [10, 20, 30, 40],
        "name":       ["a", "b", "c", "d"],
    })
    br = BlockResult(
        block_key="t",
        df=None,                       # NEW: optional when positions present
        member_positions=[1, 3],       # NEW field
    )
    out = br.materialize(df)
    assert out.height == 2
    assert out["__row_id__"].to_list() == [20, 40]
    assert out["name"].to_list() == ["b", "d"]


def test_block_result_materialize_falls_back_to_lazyframe():
    """Back-compat: when positions are absent, materialize() must collect df."""
    import polars as pl
    from goldenmatch.core.blocker import BlockResult

    inner = pl.DataFrame({"__row_id__": [1, 2], "name": ["x", "y"]}).lazy()
    br = BlockResult(block_key="t", df=inner)   # no member_positions
    out = br.materialize(parent_df=None)        # parent unused in fallback
    assert out.height == 2
    assert out["__row_id__"].to_list() == [1, 2]
```

- [ ] **Step 2: Run tests to verify they fail.**

```
cd packages/python/goldenmatch
C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_blocking.py::test_block_result_materialize_uses_positions tests/test_blocking.py::test_block_result_materialize_falls_back_to_lazyframe -v
```

Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'member_positions'` (first) and `AttributeError: 'BlockResult' object has no attribute 'materialize'` (second).

- [ ] **Step 3: Implement minimal change in `blocker.py`.**

Replace the `BlockResult` dataclass at `blocker.py:87-96` with:

```python
@dataclass
class BlockResult:
    """Result of blocking: a block key and its members.

    Two construction modes:
    - **Positional (preferred):** pass ``member_positions`` (positions into a
      parent ``pl.DataFrame`` that the caller holds). ``materialize(parent_df)``
      returns ``parent_df[positions]`` directly — no LazyFrame round-trip.
    - **LazyFrame (back-compat):** pass ``df`` only. ``materialize()`` falls
      back to ``df.collect()``.

    The positional contract is load-bearing for the Attack A perf fix; the
    LazyFrame mode stays for one release to give external consumers time to
    migrate. See spec
    ``docs/superpowers/specs/2026-05-15-post-controller-full-df-perf-design.md``.
    """

    block_key: str
    df: pl.LazyFrame | None = None
    strategy: str = "static"
    depth: int = 0
    parent_key: str | None = None
    pre_scored_pairs: list[tuple[int, int, float]] | None = None
    member_positions: list[int] | None = None

    def materialize(self, parent_df: pl.DataFrame | None) -> pl.DataFrame:
        """Return the block as an eager DataFrame.

        Positional path: ``parent_df[self.member_positions]`` (no .lazy() wrap).
        LazyFrame fallback: ``self.df.collect()`` when positions are absent.
        """
        if self.member_positions is not None:
            if parent_df is None:
                raise ValueError(
                    "BlockResult.materialize requires parent_df when "
                    "member_positions is set"
                )
            return parent_df[self.member_positions]
        if self.df is None:
            raise ValueError(
                "BlockResult.materialize requires either member_positions "
                "(with parent_df) or df (LazyFrame)"
            )
        return self.df.collect()
```

- [ ] **Step 4: Run tests to verify they pass.**

```
C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_blocking.py::test_block_result_materialize_uses_positions tests/test_blocking.py::test_block_result_materialize_falls_back_to_lazyframe -v
```

Expected: 2 passed.

- [ ] **Step 5: Run the full `tests/test_blocking.py` to confirm no regression.**

```
C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_blocking.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit.**

```
git add packages/python/goldenmatch/goldenmatch/core/blocker.py packages/python/goldenmatch/tests/test_blocking.py
git commit -m "feat(blocker): BlockResult.member_positions + materialize() helper

Adds optional positional contract to BlockResult. Spec
docs/superpowers/specs/2026-05-15-post-controller-full-df-perf-design.md
Attack A step 1. Back-compat preserved: LazyFrame mode still works via
materialize() fallback. No call-site changes yet — those follow in the
next commit."
```

### Task 1.2: Wire `apply_learned_blocks` to emit positional `BlockResult`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/learned_blocking.py:278-329` (the `apply_learned_blocks` function)
- Test: `packages/python/goldenmatch/tests/test_learned_blocking.py` (new test function appended)

- [ ] **Step 1: Write the failing test.**

Append to `packages/python/goldenmatch/tests/test_learned_blocking.py`:

```python
def test_apply_learned_blocks_emits_positions_not_lazyframe():
    """apply_learned_blocks must produce positional BlockResults — no per-block
    LazyFrame wrap. Spec Attack A step 2: kills the 462K LazyFrame.collect
    explosion. Failure mode if regressed: cProfile shows LazyFrame.collect
    ncalls back at ~1× n_rows."""
    import polars as pl
    from goldenmatch.core.learned_blocking import (
        BlockingRule, BlockingPredicate, apply_learned_blocks,
    )

    df = pl.DataFrame({
        "__row_id__": [1, 2, 3, 4, 5, 6],
        "name":       ["alice", "alice", "bob", "bob", "carol", "dave"],
    })
    rule = BlockingRule(predicates=[
        BlockingPredicate(field="name", transform="lower"),
    ])
    blocks = apply_learned_blocks(df.lazy(), [rule], max_block_size=100)

    assert len(blocks) >= 1
    for br in blocks:
        # Positional contract is mandatory in the new implementation.
        assert br.member_positions is not None, (
            "apply_learned_blocks must emit member_positions; "
            "regressed to LazyFrame mode"
        )
        # df may still be present for back-compat, but positions take priority.
        # Verify materialize uses positional path.
        materialized = br.materialize(df)
        assert materialized.height == len(br.member_positions)


def test_apply_learned_blocks_dedupe_uses_positions_not_collect():
    """The dedupe-by-member-set pass must compare position tuples, not collect
    each block to read __row_id__. Spec Attack A: 'Dedupe blocks by
    tuple(sorted(member_positions)) before constructing BlockResult.'"""
    import polars as pl
    from goldenmatch.core.learned_blocking import (
        BlockingRule, BlockingPredicate, apply_learned_blocks,
    )

    df = pl.DataFrame({
        "__row_id__": [1, 2, 3, 4],
        # Two rules that produce identical blocks — must be deduped.
        "a": ["x", "x", "y", "y"],
        "b": ["x", "x", "y", "y"],
    })
    rules = [
        BlockingRule(predicates=[BlockingPredicate(field="a", transform="lower")]),
        BlockingRule(predicates=[BlockingPredicate(field="b", transform="lower")]),
    ]
    blocks = apply_learned_blocks(df.lazy(), rules, max_block_size=100)

    # Dedup by frozenset of positions
    seen: set[frozenset[int]] = set()
    for br in blocks:
        positions_frozen = frozenset(br.member_positions or [])
        assert positions_frozen not in seen, (
            f"Duplicate block survived dedupe: {positions_frozen}"
        )
        seen.add(positions_frozen)
```

- [ ] **Step 2: Run tests to verify they fail.**

```
C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_learned_blocking.py::test_apply_learned_blocks_emits_positions_not_lazyframe tests/test_learned_blocking.py::test_apply_learned_blocks_dedupe_uses_positions_not_collect -v
```

Expected: FAIL — current implementation produces `BlockResult` with `df=block_lf` and `member_positions=None`.

- [ ] **Step 3: Rewrite `apply_learned_blocks` body.**

Replace `learned_blocking.py:278-329` with:

```python
def apply_learned_blocks(
    lf: pl.LazyFrame,
    rules: list[BlockingRule],
    max_block_size: int = 5000,
) -> list:
    """Apply learned blocking rules to produce positional BlockResult list.

    Per spec Attack A: emits BlockResult.member_positions (not a per-block
    LazyFrame wrap). Dedup by position-tuple before construction — eliminates
    the second collect pass that previously read __row_id__ per block.
    """
    from goldenmatch.core.blocker import BlockResult

    df = lf.collect()
    seen: set[tuple[int, ...]] = set()
    deduped: list = []

    for rule in rules[:3]:  # limit to top 3 rules
        rows = df.select(
            ["__row_id__"] + list({p.field for p in rule.predicates})
        ).to_dicts()

        # Build (block_key -> positions) via positional indexing into df.
        blocks: dict[str, list[int]] = {}
        for pos, row in enumerate(rows):
            key = _compute_block_key(row, rule.predicates)
            if key:
                blocks.setdefault(key, []).append(pos)

        for block_key, member_positions in blocks.items():
            if len(member_positions) < 2:
                continue
            if len(member_positions) > max_block_size:
                continue
            sorted_positions = sorted(member_positions)
            position_tuple = tuple(sorted_positions)
            if position_tuple in seen:
                continue
            seen.add(position_tuple)
            deduped.append(BlockResult(
                block_key=f"learned:{rule.key()}:{block_key}",
                df=None,                         # positional mode
                member_positions=sorted_positions,
                strategy="learned",
            ))

    logger.info(
        "Learned blocking produced %d blocks from %d rules",
        len(deduped), min(len(rules), 3),
    )
    return deduped
```

- [ ] **Step 4: Run the two new tests.**

Expected: 2 passed.

- [ ] **Step 5: Run the full `tests/test_learned_blocking.py`.**

```
C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_learned_blocking.py -v
```

Expected: all pass. If any pre-existing test reads `br.df.collect()` directly and fails, fix the test to use `br.materialize(parent_df)` — that's an intentional API migration, not a regression.

- [ ] **Step 6: Commit.**

```
git add packages/python/goldenmatch/goldenmatch/core/learned_blocking.py packages/python/goldenmatch/tests/test_learned_blocking.py
git commit -m "feat(learned_blocking): emit positional BlockResult, dedup by positions

Spec Attack A step 2. Replaces per-block lazy()/collect round-trip in
apply_learned_blocks; eliminates the second collect pass that materialized
each block to dedup by __row_id__ frozenset. Block content invariant
asserted in the new tests."
```

### Task 1.3: Wire `_score_one_block` / `score_blocks_parallel` to use `materialize(parent_df)`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/scorer.py:760-887` (`_score_one_block`, `score_blocks_parallel`)
- Modify: call sites in `packages/python/goldenmatch/goldenmatch/core/pipeline.py`, `goldenmatch/tui/engine.py`, `goldenmatch/core/chunked.py`
- Test: `packages/python/goldenmatch/tests/test_scorer.py` (new test)

- [ ] **Step 1: Identify call sites.**

```
grep -rn "score_blocks_parallel\|_score_one_block" packages/python/goldenmatch/goldenmatch/ --include="*.py"
```

Expected: callers in `pipeline.py` (dedupe + match paths), `engine.py` (TUI), `chunked.py`. Note each call site's current signature so the parent-df addition is mechanical.

- [ ] **Step 2: Write the failing test.**

Append to `packages/python/goldenmatch/tests/test_scorer.py`:

```python
def test_score_blocks_parallel_accepts_parent_df_and_positional_blocks():
    """score_blocks_parallel must accept a parent_df and materialize positional
    blocks via parent_df[positions], not via per-block LazyFrame.collect."""
    import polars as pl
    from goldenmatch.core.blocker import BlockResult
    from goldenmatch.core.scorer import score_blocks_parallel
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

    parent_df = pl.DataFrame({
        "__row_id__":   [1, 2, 3, 4],
        "__source__":   ["s", "s", "s", "s"],
        "name":         ["alice", "alyce", "bob", "robert"],
        "__mk_name__":  ["alice", "alyce", "bob", "robert"],
    })
    mk = MatchkeyConfig(
        name="name_fuzzy",
        type="weighted",
        threshold=0.5,
        fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
    )
    blocks = [
        BlockResult(
            block_key="b1", df=None, member_positions=[0, 1], strategy="learned",
        ),
        BlockResult(
            block_key="b2", df=None, member_positions=[2, 3], strategy="learned",
        ),
    ]
    pairs = score_blocks_parallel(
        blocks, mk, matched_pairs=set(), max_workers=2,
        parent_df=parent_df,
    )
    # alice~alyce should clear 0.5; bob~robert may or may not — invariant is
    # that scoring runs without LazyFrame.collect on positional blocks.
    assert isinstance(pairs, list)
```

- [ ] **Step 3: Run the test to verify it fails.**

Expected: `TypeError: score_blocks_parallel() got an unexpected keyword argument 'parent_df'`.

- [ ] **Step 4: Update `_score_one_block` and `score_blocks_parallel` signatures.**

In `scorer.py:760-787` (`_score_one_block`):

```python
def _score_one_block(
    block: Any,
    mk: MatchkeyConfig,
    exclude_pairs: set[tuple[int, int]] | frozenset[tuple[int, int]],
    across_files_only: bool = False,
    source_lookup: dict[int, str] | None = None,
    parent_df: pl.DataFrame | None = None,
) -> list[tuple[int, int, float]]:
    """Score a single block — safe to call from a thread."""
    block_df = block.materialize(parent_df)
    # ... rest unchanged ...
```

In `scorer.py:790-887` (`score_blocks_parallel`):

- Add `parent_df: pl.DataFrame | None = None` kwarg.
- Replace the two `block.df.collect()` occurrences (line 825 small-blocks fast path and line 856 candidates-counting loop) with `block.materialize(parent_df)`.
- Pass `parent_df` to `_score_one_block` in both the small-blocks path and the `ThreadPoolExecutor.submit` call.

- [ ] **Step 5: Update `score_blocks_ray` for parity** (parallel function in `backends/ray_backend.py`).

Add the same `parent_df` kwarg and use `block.materialize(parent_df)` instead of `block.df.collect()`. If the function doesn't currently collect lazily, this is a no-op signature add.

- [ ] **Step 6: Update call sites in `pipeline.py`, `engine.py`, `chunked.py`.**

In each call site, locate the `precompute_matchkey_transforms(...).lazy()` line — the result of `precompute_matchkey_transforms` is already an eager `pl.DataFrame` per the spec assumption (verified in Phase 0). Pass that eager frame as `parent_df=`.

Example, in `pipeline.py` around line 660:

```python
collected_df = precompute_matchkey_transforms(combined_lf.collect(), matchkeys)
# ... later:
pairs = block_scorer(
    blocks, mk, matched_pairs,
    parent_df=collected_df,        # NEW
    across_files_only=across_files_only,
    source_lookup=source_lookup,
)
```

If the local variable name is different (e.g. `engine.py` may use `materialized` or similar), keep the variable; just pipe it as `parent_df=`.

- [ ] **Step 7: Run the new scorer test.**

Expected: pass.

- [ ] **Step 8: Run the full scorer + chunked + pipeline tests.**

```
C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_scorer.py tests/test_chunked.py tests/test_pipeline.py -v
```

Expected: all pass. If chunked's `BlockResult` construction still wraps `LazyFrame`s, that's fine — `materialize()` falls back to `df.collect()` automatically.

- [ ] **Step 9: Commit.**

```
git add packages/python/goldenmatch/goldenmatch/core/scorer.py packages/python/goldenmatch/goldenmatch/backends/ray_backend.py packages/python/goldenmatch/goldenmatch/core/pipeline.py packages/python/goldenmatch/goldenmatch/tui/engine.py packages/python/goldenmatch/goldenmatch/core/chunked.py packages/python/goldenmatch/tests/test_scorer.py
git commit -m "feat(scorer): score_blocks_parallel accepts parent_df, uses BlockResult.materialize

Spec Attack A step 3. Replaces per-block LazyFrame.collect in
_score_one_block and the candidates-counting loop in score_blocks_parallel
with BlockResult.materialize(parent_df). Call sites in pipeline.py,
engine.py, chunked.py now pass the materialized parent df forward."
```

### Task 1.4: Static / adaptive `build_blocks` also emit positional BlockResults

Static + adaptive blocking (the non-learned strategies) also construct `BlockResult` with `df=lf`. Fold those into the positional contract while the change is small.

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/blocker.py` — `build_blocks` and helpers that construct `BlockResult`

- [ ] **Step 1: Find every `BlockResult(` constructor in `blocker.py`.**

```
grep -n "BlockResult(" packages/python/goldenmatch/goldenmatch/core/blocker.py
```

- [ ] **Step 2: For each constructor whose `df=` argument is a `lf.filter(...)` or similar lazy slice over a materialized parent**, convert to positional.

The pattern is the same as Task 1.2: materialize the parent once, build `dict[block_key, list[positions]]`, construct `BlockResult(member_positions=sorted_positions, df=None)`.

If a constructor produces a fresh LazyFrame that does **not** derive from a positional slice of the parent (e.g. a join, an aggregation), leave it on the `df=` path. `materialize()` falls back automatically.

- [ ] **Step 3: Run blocking + scoring tests + the broader pipeline tests.**

```
C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_blocking.py tests/test_scorer.py tests/test_pipeline.py tests/test_autoconfig_regressions.py -v --timeout=120
```

Expected: all pass.

- [ ] **Step 4: Commit.**

```
git add packages/python/goldenmatch/goldenmatch/core/blocker.py
git commit -m "feat(blocker): static + adaptive build_blocks emit positional BlockResults

Spec Attack A step 4. Same positional contract as apply_learned_blocks;
keeps the BlockResult interface consistent across strategies."
```

### Task 1.5: Re-bench at 500K — Attack A exit gate

Per spec §Attack A "Acceptance": median wall ≤ 250s AND `LazyFrame.collect` ncalls < 50,000 AND F1 within 0.005 of baseline.

**Files:**
- Run: `scripts/bench_1m_zero_config.py`
- Output: `.profile_tmp/bench_500k_zero_config_post_a.json`

- [ ] **Step 1: Run the bench.**

```
C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe scripts/bench_1m_zero_config.py \
  --fixture .profile_tmp/scale_fixtures/synthetic_500000_dupe15.csv \
  --runs 5 \
  --output .profile_tmp/bench_500k_zero_config_post_a.json \
  --cprofile-output .profile_tmp/bench_500k_zero_config_post_a.prof
```

Expected wall: ~17-25 min.

- [ ] **Step 2: Compare against baseline.**

Open `.profile_tmp/bench_500k_zero_config.json` (baseline) and `.profile_tmp/bench_500k_zero_config_post_a.json`. Check:

- `wall_seconds_median` ≤ 250.0 (baseline 487.7). **GATE.**
- In `cprofile_top`, the row for `<method 'collect' of 'builtins.PyLazyFrame' objects>` has ncalls < 50,000 (baseline 462,061). **GATE.**

- [ ] **Step 3: Confirm F1 invariant.**

The bench script does not compute F1. Run the audit script at 500K against ground truth to confirm F1 within 0.005 of the prior 500K F1:

```
C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe scripts/scale_audit_5m.py \
  --n-records 500000 \
  --output .profile_tmp/audit_500k_post_a.json
```

Compare `f1` field against the pre-A measurement (re-run the same command before Phase 1 if no pre-A audit-500k output exists). Tolerance: ±0.005.

- [ ] **Step 4: If gates pass, commit the bench artifacts.**

```
git add -f .profile_tmp/bench_500k_zero_config_post_a.json
git commit -m "bench(perf): post-Attack-A 500K results

Median wall: {actual}s (baseline 487.7s, gate <=250s).
LazyFrame.collect ncalls: {actual} (baseline 462,061, gate <50,000).
F1 delta vs baseline: {actual} (gate +/-0.005)."
```

- [ ] **Step 5: If gates fail, STOP.**

Open the cProfile dump in snakeviz: `python -m snakeviz .profile_tmp/bench_500k_zero_config_post_a.prof`. Identify which call site is still emitting LazyFrames. Adjust and re-bench. Do not proceed to Attack B until A's gates clear.

### Task 1.6: Open the PR for Attack A

- [ ] **Step 1: Push branch.**

```
git push -u origin perf/post-controller-full-df
```

- [ ] **Step 2: Open PR titled "perf: BlockResult positional contract (Attack A, 500K 488s -> {actual}s)".**

PR body must include:
- Link to spec.
- Before/after table for the three Attack A gates.
- Link to the committed bench JSON.
- "Attack B follow-up: see plan task Phase 2."

---

## Phase 2 — Attack B: `split_oversized_cluster` NumPy vectorization

Per spec §Attack B. Vectorize `_build_mst` and `compute_cluster_confidence` to remove the 55M Python `max()`/`min()` calls.

### Task 2.1: Add the NumPy MST helper

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/cluster.py:105-156` (`_build_mst`, `split_oversized_cluster`, `compute_cluster_confidence` — last is in same file or imported)
- Test: `packages/python/goldenmatch/tests/test_cluster.py` (new test)

- [ ] **Step 1: Write the failing property test.**

Append to `packages/python/goldenmatch/tests/test_cluster.py`:

```python
import pytest

try:
    from hypothesis import given, strategies as st, settings
    _HAS_HYPOTHESIS = True
except ImportError:
    _HAS_HYPOTHESIS = False


@pytest.mark.skipif(not _HAS_HYPOTHESIS, reason="hypothesis not installed")
@given(
    n=st.integers(min_value=3, max_value=20),
    seed=st.integers(min_value=0, max_value=10_000),
)
@settings(max_examples=200, deadline=None)
def test_split_oversized_cluster_python_equals_numpy(n: int, seed: int):
    """The vectorized split must produce the same partition as the Python
    implementation. Spec Attack B testing tier 3."""
    import random
    from goldenmatch.core.cluster import (
        split_oversized_cluster,
        _split_oversized_cluster_python,    # private kept for parity
    )
    rng = random.Random(seed)
    members = list(range(n))
    # Dense graph with random weights.
    pair_scores: dict[tuple[int, int], float] = {}
    for i in range(n):
        for j in range(i + 1, n):
            pair_scores[(i, j)] = round(rng.random(), 6)

    py = _split_oversized_cluster_python(members, pair_scores)
    nv = split_oversized_cluster(members, pair_scores)

    def normalize(parts):
        return sorted(frozenset(sc["members"]) for sc in parts)

    assert normalize(py) == normalize(nv), (
        f"Vectorized split differs from Python on n={n}, seed={seed}:\n"
        f"  python: {normalize(py)}\n"
        f"  numpy:  {normalize(nv)}"
    )
```

- [ ] **Step 2: Run the test to verify it fails.**

Expected: `ImportError: cannot import name '_split_oversized_cluster_python'` — the private parity function doesn't exist yet.

- [ ] **Step 3: Refactor + implement.**

In `packages/python/goldenmatch/goldenmatch/core/cluster.py`:

- Rename the current `split_oversized_cluster` to `_split_oversized_cluster_python` (preserve verbatim — this is the parity oracle).
- Rename `_build_mst` to `_build_mst_python` similarly.
- Add `_build_mst_numpy` and a new `split_oversized_cluster` that uses NumPy:

```python
def _build_mst_numpy(
    members: list[int], pair_scores: dict[tuple[int, int], float],
) -> list[tuple[int, int, float]]:
    """Max-weight spanning tree via NumPy-packed Kruskal.

    Eliminates the per-edge tuple unpacking that drives 55M Python max/min
    calls at 500K scale (spec Attack B).
    """
    import numpy as np
    if not pair_scores:
        return []
    n = len(pair_scores)
    edges_uv = np.empty((n, 2), dtype=np.int64)
    weights = np.empty(n, dtype=np.float64)
    for k, ((a, b), w) in enumerate(pair_scores.items()):
        edges_uv[k, 0] = a
        edges_uv[k, 1] = b
        weights[k] = w
    order = np.argsort(-weights, kind="stable")
    edges_uv = edges_uv[order]
    weights = weights[order]

    uf = UnionFind()
    uf.add_many(members)
    mst: list[tuple[int, int, float]] = []
    target_edges = len(members) - 1
    for idx in range(n):
        a = int(edges_uv[idx, 0])
        b = int(edges_uv[idx, 1])
        if uf.find(a) != uf.find(b):
            uf.union(a, b)
            mst.append((a, b, float(weights[idx])))
            if len(mst) == target_edges:
                break
    return mst


def split_oversized_cluster(
    members: list[int], pair_scores: dict[tuple[int, int], float],
) -> list[dict]:
    """Split a cluster by removing the weakest MST edge — NumPy-vectorized.

    Spec Attack B. Algorithmically identical to the prior Python version;
    parity asserted via tests/test_cluster.py::
    test_split_oversized_cluster_python_equals_numpy.
    """
    import numpy as np
    if len(members) <= 1 or not pair_scores:
        return [{"members": sorted(members), "size": len(members),
                 "oversized": False, "pair_scores": pair_scores}]

    mst = _build_mst_numpy(members, pair_scores)
    if not mst:
        return [{"members": sorted(members), "size": len(members),
                 "oversized": False, "pair_scores": pair_scores}]

    # Weakest edge by argmin (no Python min(...,key=)).
    mst_weights = np.fromiter((s for _, _, s in mst), dtype=np.float64, count=len(mst))
    weakest_idx = int(mst_weights.argmin())
    weakest = mst[weakest_idx]
    remaining = mst[:weakest_idx] + mst[weakest_idx + 1:]

    uf = UnionFind()
    uf.add_many(members)
    for a, b, _s in remaining:
        uf.union(a, b)

    result = []
    for sc_members in uf.get_clusters():
        sc_list = sorted(sc_members)
        sc_pairs = {(a, b): s for (a, b), s in pair_scores.items()
                    if a in sc_members and b in sc_members}
        size = len(sc_list)
        conf = compute_cluster_confidence(sc_pairs, size)
        result.append({
            "members": sc_list, "size": size, "oversized": False,
            "pair_scores": sc_pairs, "confidence": conf["confidence"],
            "bottleneck_pair": conf["bottleneck_pair"],
        })
    return result
```

- [ ] **Step 4: Run the property test.**

```
C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_cluster.py::test_split_oversized_cluster_python_equals_numpy -v --timeout=60
```

Expected: 200 examples pass.

- [ ] **Step 5: Run the full cluster tests.**

```
C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_cluster.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit.**

```
git add packages/python/goldenmatch/goldenmatch/core/cluster.py packages/python/goldenmatch/tests/test_cluster.py
git commit -m "perf(cluster): NumPy-vectorize MST + split_oversized_cluster

Spec Attack B. Eliminates 55M Python max/min calls in the cluster split
path by packing pair_scores into NumPy arrays, sorting once with argsort,
and using argmin for the weakest-edge pick. Parity with the prior Python
implementation asserted via Hypothesis property test (200 examples)."
```

### Task 2.2: Vectorize `compute_cluster_confidence`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/cluster.py` — `compute_cluster_confidence` (grep for the def)

- [ ] **Step 1: Locate the function.**

```
grep -n "def compute_cluster_confidence" packages/python/goldenmatch/goldenmatch/core/cluster.py
```

- [ ] **Step 2: Read the current Python implementation.**

Look for `min(scores)`, `sum(scores)/len(scores)`, and the bottleneck-pair pick. These are the 55M-call surface.

- [ ] **Step 3: Write the failing test.**

Append to `tests/test_cluster.py`:

```python
def test_compute_cluster_confidence_uses_numpy_aggregations():
    """compute_cluster_confidence should use NumPy reductions for min/mean
    instead of Python loops. We assert behavior not implementation, but the
    perf gate is enforced in the bench."""
    from goldenmatch.core.cluster import compute_cluster_confidence
    pair_scores = {(0, 1): 0.9, (0, 2): 0.8, (1, 2): 0.7}
    conf = compute_cluster_confidence(pair_scores, size=3)
    assert conf["confidence"] > 0.0
    assert conf["bottleneck_pair"] in ((1, 2), (2, 1))
```

- [ ] **Step 4: Run — should pass already.**

This test is mostly a guard for the rewrite, not a true failing-first TDD case. Run it green first to baseline, then rewrite, then run green again.

- [ ] **Step 5: Rewrite `compute_cluster_confidence` to use NumPy.**

```python
def compute_cluster_confidence(
    pair_scores: dict[tuple[int, int], float], size: int,
) -> dict:
    """Cluster confidence + bottleneck pair, NumPy-vectorized.

    Spec Attack B: replaces the Python min/avg loop that drove ~55M
    max/min calls at 500K scale.
    """
    import numpy as np
    if not pair_scores or size <= 1:
        return {"confidence": 1.0 if size <= 1 else 0.0, "bottleneck_pair": None}
    keys = list(pair_scores.keys())
    weights = np.fromiter(pair_scores.values(), dtype=np.float64, count=len(pair_scores))
    min_idx = int(weights.argmin())
    min_edge = float(weights[min_idx])
    avg_edge = float(weights.mean())
    # Connectivity: actual edges / max possible edges in a clique of `size`.
    max_edges = size * (size - 1) // 2
    connectivity = len(weights) / max_edges if max_edges else 0.0
    confidence = 0.4 * min_edge + 0.3 * avg_edge + 0.3 * connectivity
    return {
        "confidence": confidence,
        "bottleneck_pair": keys[min_idx],
    }
```

- [ ] **Step 6: Run cluster tests.**

```
C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_cluster.py -v
```

Expected: all pass, including the property test from Task 2.1 (the change to `compute_cluster_confidence` is inside `split_oversized_cluster`'s sub-cluster confidence computation; parity must hold).

- [ ] **Step 7: Commit.**

```
git add packages/python/goldenmatch/goldenmatch/core/cluster.py packages/python/goldenmatch/tests/test_cluster.py
git commit -m "perf(cluster): NumPy-vectorize compute_cluster_confidence

Spec Attack B finalization. Same algorithmic shape, NumPy reductions
instead of Python min/mean loops over the pair_scores dict."
```

### Task 2.3: Re-bench at 500K — Attack B exit gate

Per spec §Attack B "Acceptance": median wall ≤ 150s AND `builtins.max` cumtime < 30s AND F1 within 0.005.

**Files:**
- Run: `scripts/bench_1m_zero_config.py`
- Output: `.profile_tmp/bench_500k_zero_config_post_b.json`

- [ ] **Step 1: Run the bench.**

```
C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe scripts/bench_1m_zero_config.py \
  --fixture .profile_tmp/scale_fixtures/synthetic_500000_dupe15.csv \
  --runs 5 \
  --output .profile_tmp/bench_500k_zero_config_post_b.json \
  --cprofile-output .profile_tmp/bench_500k_zero_config_post_b.prof
```

- [ ] **Step 2: Check gates.**

- `wall_seconds_median` ≤ 150.0 (post-A median + ≥60s reduction). **GATE.**
- In `cprofile_top`, no `builtins.max` entry above 30s cumtime. **GATE.**

- [ ] **Step 3: F1 invariant.**

```
C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe scripts/scale_audit_5m.py \
  --n-records 500000 \
  --output .profile_tmp/audit_500k_post_b.json
```

F1 within 0.005 of post-A audit. **GATE.**

- [ ] **Step 4: Commit bench artifacts.**

```
git add -f .profile_tmp/bench_500k_zero_config_post_b.json
git commit -m "bench(perf): post-Attack-B 500K results

Median wall: {actual}s (post-A {prev}s, gate <=150s).
builtins.max cumtime: {actual}s (gate <30s).
F1 delta vs post-A: {actual} (gate +/-0.005)."
```

### Task 2.4: Open the PR for Attack B

- [ ] **Step 1: Push (existing branch or new — author's call; spec says two PRs).**

If continuing on `perf/post-controller-full-df`, push and open the second PR off the same branch only after Attack A's PR is merged. Otherwise: `git switch -c perf/post-controller-full-df-attack-b` and push.

- [ ] **Step 2: Open PR titled "perf: NumPy-vectorize cluster split (Attack B, 500K {prev}s -> {actual}s)".**

Body: spec link, gate table, bench JSON link.

---

## Phase 3 — 1M headline measurement + docs

### Task 3.1: 1M re-bench (the spec's actual exit criterion)

Per spec §Acceptance criteria step 3. Single run, captured for the CHANGELOG.

**Files:**
- Run: `scripts/bench_1m_zero_config.py`
- Output: `.profile_tmp/bench_1m_zero_config_after.json`

- [ ] **Step 1: Generate the 1M fixture if absent.**

```
ls .profile_tmp/scale_fixtures/synthetic_1000000_dupe15.csv
```

If missing, regenerate via `scripts/scale_audit_5m_generate.py` (already used for the prior 1M cloud audit).

- [ ] **Step 2: Run the bench.**

```
C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe scripts/bench_1m_zero_config.py \
  --fixture .profile_tmp/scale_fixtures/synthetic_1000000_dupe15.csv \
  --runs 5 \
  --output .profile_tmp/bench_1m_zero_config_after.json \
  --cprofile-output .profile_tmp/bench_1m_zero_config_after.prof
```

Expected wall per run: ~5 min after both attacks. Total ~30 min.

- [ ] **Step 3: Confirm headline gate.**

`wall_seconds_median` ≤ 480 (8 min, per spec). If above, run cProfile analysis to identify the new top hotspot, but **do not** retro-fit the spec. Surface as a follow-up.

- [ ] **Step 4: Commit the JSON.**

```
git add -f .profile_tmp/bench_1m_zero_config_after.json
git commit -m "bench(perf): post-A+B 1M headline result

Median wall: {actual}s (baseline 1M cloud ~1237s with double-run bug,
laptop-equivalent baseline projected ~16 min). Spec acceptance criterion 3."
```

### Task 3.2: CHANGELOG + README callout

**Files:**
- Modify: `packages/python/goldenmatch/CHANGELOG.md`
- Modify (conditional): `README.md` "what's new" section if delta > 50%

- [ ] **Step 1: Append CHANGELOG entry under the next unreleased section.**

```markdown
### Performance

- Default-backend `gm.dedupe_df(df)` zero-config is ~{X}× faster at 500K.
  Two cross-stage Python hotspots eliminated: (A) the per-block
  `LazyFrame.collect` round-trip in `apply_learned_blocks` (was 462K
  collects at 500K) is replaced with a positional `BlockResult` contract
  that materializes via `parent_df[positions]` once at the single scoring
  consumer; (B) `split_oversized_cluster` + `compute_cluster_confidence`
  are NumPy-vectorized (eliminates ~55M Python `max()`/`min()` calls).
  500K median wall: 488s -> {actual}s. 1M median: {actual_1m}s. See
  `docs/superpowers/specs/2026-05-15-post-controller-full-df-perf-design.md`.
```

- [ ] **Step 2: If 1M median dropped > 50%, add a one-line callout to README.md** under the existing "what's new" or performance section. Match the formatting of prior callouts.

- [ ] **Step 3: Commit.**

```
git add packages/python/goldenmatch/CHANGELOG.md README.md
git commit -m "docs(changelog): record post-controller full-df perf wins (Attacks A + B)"
```

### Task 3.3: Memory entry — capture the cross-stage-primitive lesson

Per spec §Implementation order step 6 and reviewer advisory #3.

**Files:**
- Create: `C:\Users\bsevern\.claude\projects\D--show-case-goldenmatch\memory\project_post_controller_full_df_perf.md`
- Modify: `C:\Users\bsevern\.claude\projects\D--show-case-goldenmatch\memory\MEMORY.md`

- [ ] **Step 1: Create the memory file.**

```markdown
---
name: project-post-controller-full-df-perf
description: Post-controller full-df perf attack (May 2026) — cross-stage primitives owned 40% of wall, not single stages. Lesson for future perf audits.
metadata:
  type: project
---

The 500K bench of `gm.dedupe_df(df)` showed no single pipeline stage cleared
30% of wall — blocking, scoring, and clustering each owned ~22-24%. The real
bottlenecks were two **cross-stage Python primitives**: 462K `LazyFrame.collect`
calls (27% wall) and 55M `builtins.max` calls (13% wall). Both fixed in
PRs (Attack A + Attack B, see `docs/superpowers/specs/2026-05-15-post-controller-full-df-perf-design.md`).

**Why:** Earlier audits ranked by per-stage cumtime and missed cross-stage costs.
The spec's CLAUDE.md performance-audit lesson ("rank by measured wall, not
static structure") captured the rule; this audit was the first to actually
apply it at scale.

**How to apply:** When profiling a multi-stage pipeline, always group cProfile
top by *primitive* (tottime over function name, ignoring caller chain) in
addition to grouping by stage subtree. If a primitive appears across multiple
stages, that's a cross-cutting hotspot that no single stage owns.

Related: [[reference-bench-1m-zero-config-script]] if I write that memory later.
```

- [ ] **Step 2: Add the index line to `MEMORY.md`.**

```
- [Post-controller full-df perf lessons](project_post_controller_full_df_perf.md) — cross-stage primitives owned 40% wall; rank cProfile by primitive too
```

- [ ] **Step 3: No git commit (memory is outside the repo).**

---

## Phase 4 — Cleanup + follow-ups (out of scope but tracked)

### Task 4.1: Fix the bench script's stage-monkey-patch

Per spec §Caveats from the bench (1) and reviewer advisory #2. Source-module `setattr` doesn't catch `from X import Y` call-site bindings. Fix to patch at `pipeline.py` call sites or use `unittest.mock.patch.object` with `wraps=`.

**Files:**
- Modify: `scripts/bench_1m_zero_config.py:install_stage_patches`

- [ ] **Step 1: Replace source-module patching with call-site patching.**

For each target (`compute_matchkeys`, `precompute_matchkey_transforms`, etc.) patch on `goldenmatch.core.pipeline` (the import site) instead of the source module. Same for any other call-site module the function is invoked from.

- [ ] **Step 2: Run the bench with `--runs 1 --skip-cprofile` to verify per-stage timings now populate.**

- [ ] **Step 3: Commit.**

```
git add scripts/bench_1m_zero_config.py
git commit -m "fix(bench): patch stage timers at call site, not source module

Previous version patched the source module via setattr, which 'from X
import Y' call sites never see. The bench JSON for the May 2026 audit
shows only _apply_domain_extraction firing because that one is called
via the module-qualified name. cProfile carried that audit; this fix
makes future bench runs self-attribute correctly."
```

### Task 4.2: Filed-as-followups (do NOT do as part of this plan)

These were flagged in the spec but are out of scope. File them as GitHub issues with `perf` label after Phase 3 lands:

- GoldenCheck quality scan firing 3× per `dedupe_df` call (~5-6% wall).
- Controller committing RED on the synthetic 500K fixture (`failing_subprofile=scoring`).
- `score_blocks_parallel` ThreadPoolExecutor scheduling causing 22% wall variance — investigate pinning `max_workers`.
- chunked / DuckDB backend parity: apply analogous positional + NumPy-vectorize patterns if 5M profiling shows the same primitives dominate.

---

## Acceptance checklist (matches spec §Acceptance criteria)

- [ ] Attack A PR merged. 500K bench median ≤ 250s, `LazyFrame.collect` ncalls < 50,000, F1 within 0.005.
- [ ] Attack B PR merged. 500K bench median ≤ 150s, `builtins.max` cumtime < 30s, F1 within 0.005.
- [ ] 1M bench re-run committed to `.profile_tmp/bench_1m_zero_config_after.json`, median ≤ 8 min.
- [ ] CHANGELOG entry merged with measured before/after deltas. README callout if delta > 50%.
- [ ] Memory entry `project_post_controller_full_df_perf.md` written.
- [ ] Existing test suite (1572+ passing per CLAUDE.md baseline) green.
