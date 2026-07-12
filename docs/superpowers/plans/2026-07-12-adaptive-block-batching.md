# Adaptive Block-Batching in the Parallel Fuzzy Scorer — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the ~62K per-block `ThreadPoolExecutor` futures in `score_blocks_parallel` (and its columnar twin) into a few dozen adaptive work units, cutting the `as_completed`/lock orchestration wall while producing byte-identical clusters.

**Architecture:** Add a cheap `BlockResult.n_rows` (populated where the size is already computed for free at block construction). A pure `_plan_block_batches` planner groups blocks adaptively — big blocks (many candidate pairs) stay solo for full parallelism, small blocks are LPT bin-packed into `~max_workers × K` bins. A uniform `_score_block_batch` work fn loops `_score_one_block` over its batch. Both `score_blocks_parallel` and `score_blocks_columnar` submit one future per batch instead of per block. Scoring math is untouched, so the pair set (and therefore clustering) is invariant.

**Tech Stack:** Python 3.12, Polars, rapidfuzz, `concurrent.futures.ThreadPoolExecutor`, pytest. Worktree: `D:\show_case\gm-block-batching` on branch `feat/scorer-adaptive-block-batching` (off `origin/main` @ `5d17d3eba`).

**Spec:** `docs/superpowers/specs/2026-07-12-adaptive-block-batching-design.md`

**Testing constraint:** The local box OOMs on the 500K bench — all at-scale verification is remote via the `bench-zero-config` GitHub workflow. Unit tests use small fixtures and run locally. Per the worktree test memory: run Python via the main `.venv` with `PYTHONPATH` to this worktree, `POLARS_SKIP_CPU_CHECK=1`, `PYTHONIOENCODING=utf-8`, `GOLDENMATCH_NATIVE=0`.

---

## File Structure

- **Modify** `packages/python/goldenmatch/goldenmatch/core/blocker.py`
  - `BlockResult` dataclass (line ~242): add `n_rows: int | None = None`.
  - Populate `n_rows` at the construction sites where a row count is already in hand (see Task 1).
- **Modify** `packages/python/goldenmatch/goldenmatch/core/scorer.py`
  - Add module constants + env resolution for the two tunables.
  - Add `_plan_block_batches(blocks, max_workers)` planner.
  - Add `_score_block_batch(...)` (list path) and `_score_block_batch_columnar(...)` (columnar path).
  - Rewire `score_blocks_parallel` (line ~1413) and `score_blocks_columnar` (line ~1849) to submit per-batch.
- **Create/Modify** tests:
  - `packages/python/goldenmatch/tests/test_scorer_batching.py` (new) — planner + batch-equivalence.
  - `packages/python/goldenmatch/tests/test_blocker.py` (existing, or new `test_blocker_n_rows.py`) — `n_rows` population.

Reference the local-run pattern with @superpowers helpers if needed; the concrete run commands are inline below.

**Run-prefix used throughout (a single line, adjust drive if needed):**

```bash
PP="D:/show_case/gm-block-batching/packages/python/goldenmatch;D:/show_case/gm-block-batching/packages/python/goldencheck;D:/show_case/gm-block-batching/packages/python/goldencheck-types;D:/show_case/gm-block-batching/packages/python/goldenflow;D:/show_case/gm-block-batching/packages/python/goldenpipe;D:/show_case/gm-block-batching/packages/python/infermap"
RUN="PYTHONPATH=$PP POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 GOLDENMATCH_NATIVE=0 /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest"
```

(If the main `.venv` python path differs, use `.venv/Scripts/python.exe` under the primary checkout. The worktree has no venv of its own — this is the documented worktree pattern.)

---

## Task 1: Add `BlockResult.n_rows` and populate it at cheap sites

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/blocker.py:242-250` (dataclass)
- Modify: `blocker.py` construction sites (lines below)
- Test: `packages/python/goldenmatch/tests/test_blocker.py` (add a test; if the file is large, create `tests/test_blocker_n_rows.py`)

**Background:** `BlockResult` currently has no size field and `df` is a `pl.LazyFrame`, so getting a block's size costs a `.collect()`. Doing that per-block at 62K–1.67M blocks is the documented OOM leak (PRs #295/#301/#303). The fix: thread the size that is ALREADY computed at construction into a new optional field.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_blocker.py` (imports at top of file per the ruff cascade-delete gotcha):

```python
def test_block_result_n_rows_populated_on_static_path():
    """build_blocks populates BlockResult.n_rows from the group size that is
    already computed at construction (no extra .collect())."""
    import polars as pl
    from goldenmatch.core.blocker import build_blocks
    from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig

    df = pl.DataFrame({
        "__row_id__": list(range(6)),
        "city": ["nyc", "nyc", "nyc", "la", "la", "sf"],
    })
    config = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["city"], transforms=["lowercase"])],
    )
    blocks = build_blocks(df.lazy(), config)
    # Every block from the static group path carries a non-None size matching
    # its actual row count.
    assert blocks, "expected at least one block"
    for b in blocks:
        assert b.n_rows is not None, f"block {b.block_key!r} has n_rows=None"
        assert b.n_rows == b.df.collect().height
```

- [ ] **Step 2: Run test to verify it fails**

Run: `eval $RUN tests/test_blocker.py::test_block_result_n_rows_populated_on_static_path -v`
Expected: FAIL — `n_rows` attribute is `None` (field default) because construction sites don't set it yet. (If `BlockingConfig`/`build_blocks` signature differs, adjust the fixture to match the real API — read `build_blocks` first.)

- [ ] **Step 3: Add the field**

`blocker.py` dataclass (after `pre_scored_pairs`, line ~250):

```python
    pre_scored_pairs: list[tuple[int, int, float]] | None = None
    n_rows: int | None = None
    """Row count of this block when known cheaply at construction (group size).
    None when the constructing path can't supply it for free — the scorer's
    batch planner treats None as 'small' and round-robin bins it. NEVER derive
    this via a per-block .collect() at call sites (the #295/#301/#303 OOM leak)."""
```

- [ ] **Step 4: Populate at the cheap construction sites**

At each site below the size (or a cheap `len(...)`) is already in local scope. Add `n_rows=<value>` to the `BlockResult(...)` call. Exact sites (main @ 5d17d3eba):

| Line | Construction | `n_rows` value |
|------|--------------|----------------|
| ~470 | static group loop (`size = len(group_df)` @ ~385) — **the 62K-block hot path** | `n_rows=size` |
| ~580 | ANN sub-block (`sub_df = block_df[member_list]`) | `n_rows=len(member_list)` (NOT the parent `size`) |
| ~661 | adaptive sub-block group loop (`size = len(group_df)` @ ~640) | `n_rows=size` |
| ~735 | auto-split sub-block (`group_frame.height`) | `n_rows=group_frame.height` |
| ~788 | sorted-neighborhood single window (whole df, `n` in scope) | `n_rows=n` |
| ~798 | sorted-neighborhood sliding window (`window_size`) | `n_rows=window_size` |
| ~890 | ANN block (`block_df = df[member_list]`) | `n_rows=len(member_list)` |
| ~1052 | canopy block (`block_df = df[sorted(list(members))]`) | `n_rows=len(members)` |

Leave `n_rows=None` (do nothing) at the genuinely-lazy fallbacks: ~618 (max-depth), ~689 (auto-split no-columns), ~747 (auto-split empty-results), ~928 (ANN-pairs, whole-df + `pre_scored_pairs`). These are rare and degrade safely to round-robin. Do NOT add a `.collect()` to populate them.

- [ ] **Step 5: Run test to verify it passes**

Run: `eval $RUN tests/test_blocker.py::test_block_result_n_rows_populated_on_static_path -v`
Expected: PASS

- [ ] **Step 6: Regression-check the blocker suite**

Run: `eval $RUN tests/test_blocker.py -q`
Expected: all pass (the new field is additive with a default; no existing behavior changes).

- [ ] **Step 7: Commit**

```bash
cd /d/show_case/gm-block-batching
git add packages/python/goldenmatch/goldenmatch/core/blocker.py packages/python/goldenmatch/tests/test_blocker.py
git commit -m "feat(blocker): add cheap BlockResult.n_rows for scorer batch planning"
```

---

## Task 2: `_plan_block_batches` planner (pure function, TDD)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/scorer.py` (add constants + planner near `_DEFAULT_MAX_WORKERS`, line ~1385)
- Test: `packages/python/goldenmatch/tests/test_scorer_batching.py` (new)

**Background:** Pure grouping logic, no scoring, no Polars materialization. Reads only `block.n_rows`. This is the load-bearing correctness/perf unit — test it thoroughly in isolation.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scorer_batching.py`:

```python
"""Unit tests for the scorer's block-batch planner (adaptive block-batching)."""
from dataclasses import dataclass


@dataclass
class _FakeBlock:
    """Stand-in for BlockResult — the planner only reads .n_rows and identity."""
    block_key: str
    n_rows: int | None


def _pairs(n):
    return n * (n - 1) // 2


def test_empty_blocks_empty_plan():
    from goldenmatch.core.scorer import _plan_block_batches
    assert _plan_block_batches([], max_workers=4) == []


def test_big_blocks_go_solo(monkeypatch):
    from goldenmatch.core import scorer
    monkeypatch.setattr(scorer, "_SOLO_BLOCK_MIN_PAIRS", 1000)
    big = _FakeBlock("big", n_rows=100)      # 4950 pairs >= 1000 -> solo
    small = _FakeBlock("small", n_rows=3)    # 3 pairs -> binned
    batches = scorer._plan_block_batches([big, small], max_workers=4)
    solo = [b for b in batches if len(b) == 1 and b[0].block_key == "big"]
    assert len(solo) == 1, "big block must be its own batch"


def test_small_blocks_bin_into_bounded_count(monkeypatch):
    from goldenmatch.core import scorer
    monkeypatch.setattr(scorer, "_SOLO_BLOCK_MIN_PAIRS", 10_000)
    monkeypatch.setattr(scorer, "_BATCH_BINS_PER_WORKER", 4)
    blocks = [_FakeBlock(f"b{i}", n_rows=2) for i in range(1000)]  # all tiny
    batches = scorer._plan_block_batches(blocks, max_workers=8)
    # bounded by max_workers * K = 32 bins
    assert len(batches) <= 32
    # every block appears exactly once
    seen = [blk.block_key for batch in batches for blk in batch]
    assert sorted(seen) == sorted(b.block_key for b in blocks)


def test_none_n_rows_round_robin_still_batches(monkeypatch):
    from goldenmatch.core import scorer
    monkeypatch.setattr(scorer, "_BATCH_BINS_PER_WORKER", 4)
    blocks = [_FakeBlock(f"b{i}", n_rows=None) for i in range(500)]
    batches = scorer._plan_block_batches(blocks, max_workers=8)
    assert 0 < len(batches) <= 32
    seen = [blk.block_key for batch in batches for blk in batch]
    assert sorted(seen) == sorted(b.block_key for b in blocks)


def test_every_block_scored_exactly_once_mixed(monkeypatch):
    from goldenmatch.core import scorer
    monkeypatch.setattr(scorer, "_SOLO_BLOCK_MIN_PAIRS", 1000)
    blocks = (
        [_FakeBlock(f"big{i}", n_rows=200) for i in range(3)]     # solo
        + [_FakeBlock(f"sm{i}", n_rows=2) for i in range(100)]    # binned
        + [_FakeBlock(f"nn{i}", n_rows=None) for i in range(10)]  # round-robin
    )
    batches = scorer._plan_block_batches(blocks, max_workers=4)
    seen = [blk.block_key for batch in batches for blk in batch]
    assert sorted(seen) == sorted(b.block_key for b in blocks)
    assert len(seen) == len(blocks), "no block duplicated or dropped"
```

- [ ] **Step 2: Run to verify failure**

Run: `eval $RUN tests/test_scorer_batching.py -v`
Expected: FAIL — `_plan_block_batches` / `_SOLO_BLOCK_MIN_PAIRS` don't exist yet (ImportError/AttributeError).

- [ ] **Step 3: Implement constants + planner**

In `scorer.py` near `_DEFAULT_MAX_WORKERS` (~line 1385):

```python
import os


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# Blocks whose candidate-pair count (n*(n-1)/2) reaches this get their own
# future (full parallelism where scoring work is real). Below it, blocks are
# bin-packed. Default pinned by the bench sweep in Task 6.
_SOLO_BLOCK_MIN_PAIRS = _env_int("GOLDENMATCH_SCORER_SOLO_BLOCK_MIN_PAIRS", 10_000)

# Number of small-block bins per worker (bins = max_workers * this).
_BATCH_BINS_PER_WORKER = _env_int("GOLDENMATCH_SCORER_BATCH_BINS_PER_WORKER", 4)


def _plan_block_batches(blocks, max_workers):
    """Group blocks into a small number of work units.

    Adaptive: a block with >= _SOLO_BLOCK_MIN_PAIRS candidate pairs (from its
    cheap .n_rows) becomes its own single-element batch. All other blocks --
    including any with n_rows=None -- are distributed by greedy LPT bin-packing
    into at most ``max_workers * _BATCH_BINS_PER_WORKER`` bins, balancing bins
    by summed candidate-pair count (None counted as 1, so round-robin-ish).

    Pure function: reads only block.n_rows. NEVER materializes a block.
    Returns list[list[block]]; every input block appears in exactly one batch.
    """
    if not blocks:
        return []

    solo = []
    small = []
    for b in blocks:
        n = getattr(b, "n_rows", None)
        if n is not None and (n * (n - 1) // 2) >= _SOLO_BLOCK_MIN_PAIRS:
            solo.append([b])
        else:
            small.append(b)

    batches = list(solo)

    if small:
        n_bins = min(len(small), max(1, max_workers * _BATCH_BINS_PER_WORKER))
        # LPT: heaviest first, drop each onto the currently-lightest bin.
        def _cost(b):
            n = getattr(b, "n_rows", None) or 1
            return n * (n - 1) // 2 if n > 1 else 1
        ordered = sorted(small, key=_cost, reverse=True)
        bins = [[] for _ in range(n_bins)]
        loads = [0] * n_bins
        for b in ordered:
            j = min(range(n_bins), key=lambda i: loads[i])
            bins[j].append(b)
            loads[j] += _cost(b)
        batches.extend(bin_ for bin_ in bins if bin_)

    return batches
```

- [ ] **Step 4: Run to verify pass**

Run: `eval $RUN tests/test_scorer_batching.py -v`
Expected: PASS (all 5)

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/scorer.py packages/python/goldenmatch/tests/test_scorer_batching.py
git commit -m "feat(scorer): adaptive block-batch planner (_plan_block_batches)"
```

---

## Task 3: `_score_block_batch` work fn + byte-identical wiring of `score_blocks_parallel`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/scorer.py` (add `_score_block_batch`; rewire `score_blocks_parallel` ~1413-1560)
- Test: `packages/python/goldenmatch/tests/test_scorer_batching.py` (add equivalence test)

**Background:** The batch fn must call `_score_one_block` with the SAME arguments as today (same `mk`, `frozen_exclude`, `across_files_only`, `source_lookup`) so the pair set is unchanged. Read `_score_one_block`'s exact signature (scorer.py:1327) and the current submit call (`executor.submit(_score_one_block, block, mk, frozen_exclude, across_files_only, source_lookup)`, ~1532) before writing — match it exactly.

- [ ] **Step 1: Write the failing equivalence test**

Add to `tests/test_scorer_batching.py`:

```python
def _mixed_person_frame():
    import polars as pl
    # A few multi-row blocks (share surname) + many singletons, so the planner
    # exercises both solo and binned paths.
    rows = []
    rid = 0
    for surname, n in [("smith", 6), ("jones", 5), ("lee", 4)]:
        for k in range(n):
            rows.append({"__row_id__": rid, "first": f"john{k%2}", "last": surname})
            rid += 1
    for i in range(40):  # singletons
        rows.append({"__row_id__": rid, "first": f"uniq{i}", "last": f"sur{i}"})
        rid += 1
    return pl.DataFrame(rows)


def _blocks_and_mk(df):
    from goldenmatch.core.blocker import build_blocks
    from goldenmatch.config.schemas import (
        BlockingConfig, BlockingKeyConfig, MatchkeyConfig, MatchkeyField,
    )
    blocking = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["last"], transforms=["lowercase"])],
    )
    blocks = build_blocks(df.lazy(), blocking)
    # NOTE (verified against schemas.py): MatchkeyConfig.name is REQUIRED (no
    # default); type is Literal["exact","weighted","probabilistic"]|None (there
    # is no "fuzzy"); a "weighted" matchkey requires every field to carry BOTH
    # scorer AND weight. Use "weighted" for a fuzzy-scored single field.
    mk = MatchkeyConfig(
        name="mk", type="weighted", threshold=0.7,
        fields=[MatchkeyField(field="first", scorer="jaro_winkler", weight=1.0)],
    )
    return blocks, mk


def _per_block_reference(blocks, mk):
    """Ground truth = today's behavior: score each block directly, no batching.
    Comparing against THIS (not against _SOLO_BLOCK_MIN_PAIRS=0, which still
    routes through _score_block_batch) makes the unit test prove byte-identity
    vs the pre-batching path, not batching-vs-batching."""
    from goldenmatch.core.scorer import _score_one_block
    out = []
    for b in blocks:
        out.extend(_score_one_block(b, mk, set(), across_files_only=False,
                                    source_lookup=None))
    return out


def test_batched_equals_per_block(monkeypatch):
    """score_blocks_parallel with batching yields the SAME pair set as scoring
    each block directly (the pre-batching behavior). Byte-identical guarantee."""
    from goldenmatch.core import scorer
    df = _mixed_person_frame()
    blocks, mk = _blocks_and_mk(df)

    ref = _per_block_reference(blocks, mk)

    monkeypatch.setattr(scorer, "_SOLO_BLOCK_MIN_PAIRS", 10_000)
    monkeypatch.setattr(scorer, "_BATCH_BINS_PER_WORKER", 4)
    got = scorer.score_blocks_parallel(list(blocks), mk, set(), max_workers=4)

    norm = lambda ps: sorted((min(a, b), max(a, b), round(s, 6)) for a, b, s in ps)
    assert norm(got) == norm(ref)
    assert got, "sanity: the smith/jones/lee blocks should yield some pairs"
```

(Adjust `build_blocks`/schema kwargs to the real API if anything drifts — read `config/schemas.py` and `blocker.build_blocks` first. Keep the intent: batched output == direct per-block output, normalized pair set.)

- [ ] **Step 2: Run to establish the current baseline**

Run: `eval $RUN tests/test_scorer_batching.py::test_batched_equals_per_block -v`
Expected: On the UNMODIFIED `score_blocks_parallel` (still per-block), this PASSES — because the reference is a direct per-block loop and the function currently does the same thing. That's fine: the test's job is to LOCK equivalence so that Step 3's rewiring can't silently change the pair set. It becomes a genuine guard the moment batching is active (Step 3). If it FAILS here, the fixture/API is wrong — fix the fixture before touching `scorer.py`.

- [ ] **Step 3: Implement `_score_block_batch` and rewire**

Add near `_score_one_block` (after ~1385):

```python
def _score_block_batch(batch, mk, frozen_exclude, across_files_only, source_lookup):
    """Score every block in one batch on a single worker thread.

    Loops _score_one_block with the SAME args the per-block path used, so the
    emitted pairs are identical. A solo (big) block is a batch of one, so this
    is the uniform executor work unit.
    """
    out = []
    for block in batch:
        out.extend(
            _score_one_block(
                block, mk, frozen_exclude,
                across_files_only=across_files_only,
                source_lookup=source_lookup,
            )
        )
    return out
```

(Match the real `_score_one_block` signature — verify whether `across_files_only`/`source_lookup` are positional or keyword at the existing submit call ~1532 and mirror it exactly.)

Rewire the parallel submit loop in `score_blocks_parallel` (~1525-1560). Replace:

```python
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {}
        for i, block in enumerate(blocks):
            future = executor.submit(
                _score_one_block, block, mk, frozen_exclude,
                across_files_only, source_lookup,
            )
            future_to_idx[future] = i
        completed = 0
        for future in as_completed(future_to_idx):
            pairs = future.result()
            ...
```

with:

```python
    batches = _plan_block_batches(blocks, max_workers)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {}
        for i, batch in enumerate(batches):
            future = executor.submit(
                _score_block_batch, batch, mk, frozen_exclude,
                across_files_only, source_lookup,
            )
            future_to_idx[future] = i
        completed = 0
        n_units = len(batches)
        for future in as_completed(future_to_idx):
            pairs = future.result()
            # (unchanged) target_ids filter + all_pairs.extend + matched_pairs.add
            ...
            completed += 1
            if n_units and completed % max(n_units // 10, 1) == 0:
                logger.info("Scoring progress: %d/%d batches, %d pairs so far",
                            completed, n_units, len(all_pairs))
```

Keep everything else identical: the `len(blocks) <= 2` shortcut (~1460), the `frozen_exclude = frozenset(matched_pairs)` snapshot, the `_CANDIDATE_COUNT_SKIP_THRESHOLD` loop, the `target_ids` post-filter, `matched_pairs.add`, and the final `_emit_scoring_profile`. The `future.result()` now returns a batch's concatenated pairs; the collection-loop body (filter + extend + matched_pairs update) is unchanged because it already operates on a list of pairs.

- [ ] **Step 4: Run the equivalence + planner tests**

Run: `eval $RUN tests/test_scorer_batching.py -v`
Expected: PASS (equivalence holds; batching active).

- [ ] **Step 5: Run the scorer suite**

Run: `eval $RUN tests/test_scorer.py -q`
Expected: all pass (byte-identical behavior; `score_blocks_parallel` is exercised widely).

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/scorer.py packages/python/goldenmatch/tests/test_scorer_batching.py
git commit -m "feat(scorer): batch per-work-unit in score_blocks_parallel (byte-identical)"
```

---

## Task 4: Mirror in `score_blocks_columnar`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/scorer.py` (add `_score_block_batch_columnar`; rewire `score_blocks_columnar` ~1849-1960)
- Test: `packages/python/goldenmatch/tests/test_scorer_batching.py`

**Background:** `score_blocks_columnar` (~1849) has the identical per-block submit pattern and mutates `matched_pairs` per-block before `pl.concat` (~1918/1953) "so order is consistent with the list path." The batch fn must preserve that per-block `matched_pairs.add` ordering. Read the columnar function fully before editing.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_scorer_batching.py`:

```python
def test_columnar_batched_equals_per_block(monkeypatch):
    from goldenmatch.core import scorer
    df = _mixed_person_frame()
    blocks, mk = _blocks_and_mk(df)

    # Reference = direct per-block columnar scoring (pre-batching behavior).
    from goldenmatch.core.scorer import _score_one_block_columnar
    import polars as pl
    ref_frames = [
        _score_one_block_columnar(b, mk, set(), across_files_only=False,
                                  source_lookup=None)
        for b in blocks
    ]
    ref_frames = [f for f in ref_frames if f is not None and f.height]
    ref = pl.concat(ref_frames) if ref_frames else None

    monkeypatch.setattr(scorer, "_SOLO_BLOCK_MIN_PAIRS", 10_000)
    got = scorer.score_blocks_columnar(list(blocks), mk, set(), max_workers=4)

    # score_blocks_columnar returns a Polars DataFrame of pairs; compare as a
    # normalized set of (min,max,score).
    def _norm(dfp):
        import polars as pl
        if dfp is None or dfp.height == 0:
            return []
        cols = dfp.columns
        a, b, s = cols[0], cols[1], cols[2]
        rows = dfp.select([a, b, s]).iter_rows()
        return sorted((min(x, y), max(x, y), round(float(sc), 6)) for x, y, sc in rows)

    assert _norm(got) == _norm(ref)
```

(Confirm the columnar return type + column names from the function body; adjust `_norm` accordingly.)

- [ ] **Step 2: Run to verify (passes trivially pre-wiring, like Task 3 Step 2)**

Run: `eval $RUN tests/test_scorer_batching.py::test_columnar_batched_equals_per_block -v`

- [ ] **Step 3: Implement + rewire**

Add:

```python
def _score_block_batch_columnar(batch, mk, frozen_exclude, across_files_only,
                                source_lookup):
    """Columnar batch worker. Loops _score_one_block_columnar and concatenates
    into a single pair-stream DataFrame (None if the batch produced no pairs).
    No matched_pairs mutation here -- the real columnar path updates
    matched_pairs in the MAIN collection loop (scorer.py:~1946-1953), and those
    (min,max) adds are set-based / order-invariant, so the collection loop stays
    the sole writer over the batch's concatenated frame."""
    frames = []
    for block in batch:
        dfp = _score_one_block_columnar(
            block, mk, frozen_exclude,
            across_files_only=across_files_only,
            source_lookup=source_lookup,
        )
        if dfp is not None and dfp.height:
            frames.append(dfp)
    if not frames:
        return None
    return _concat_pair_frames(frames)  # reuse the existing PAIR_STREAM_SCHEMA helper
```

Wire `score_blocks_columnar` to submit `_score_block_batch_columnar` per batch, mirroring Task 3. IMPORTANT: the real columnar path updates `matched_pairs` in the **main collection loop** (verified scorer.py:~1946-1953), NOT in the worker — keep it there, iterating the batch's concatenated frame exactly as today's code iterates a single block's frame. Use `_concat_pair_frames` (the existing helper) not a bare `pl.concat`, so the pair-stream schema stays consistent.

- [ ] **Step 4: Run tests**

Run: `eval $RUN tests/test_scorer_batching.py -v && eval $RUN tests/test_scorer.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/scorer.py packages/python/goldenmatch/tests/test_scorer_batching.py
git commit -m "feat(scorer): mirror batch-per-work-unit in score_blocks_columnar"
```

---

## Task 5: Broader local regression + lint

**Files:** none (verification only)

- [ ] **Step 1: Run the fuzzy/pipeline-adjacent suites**

Run:
```bash
eval $RUN tests/test_scorer.py tests/test_blocker.py tests/test_scorer_batching.py tests/test_pipeline.py tests/test_cluster.py -q
```
Expected: all pass. (Skip torch/embedder tests per the machine gotchas; if `test_pipeline.py` pulls a cross-encoder download, run the subset that doesn't, per the CLAUDE.md offline pattern.)

- [ ] **Step 2: Lint the touched files**

Run:
```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m ruff check packages/python/goldenmatch/goldenmatch/core/scorer.py packages/python/goldenmatch/goldenmatch/core/blocker.py packages/python/goldenmatch/tests/test_scorer_batching.py
```
Expected: clean (E9/F63/F7/F82 are the CI-enforced set; fix anything flagged).

- [ ] **Step 3: Push branch + open PR (do NOT merge yet — bench gates first)**

```bash
cd /d/show_case/gm-block-batching
unset GH_TOKEN; gh auth switch --user benzsevern; export GH_TOKEN=$(gh auth token --user benzsevern)
git push -u origin feat/scorer-adaptive-block-batching
gh pr create --repo benseverndev-oss/goldenmatch --title "perf(scorer): adaptive block-batching in the parallel fuzzy scorer" \
  --body "Collapses ~62K per-block futures into adaptive batches (big blocks solo, small blocks LPT-binned). Byte-identical clusters. Spec + plan local. Bench verification pending (Task 6)."
```

Do NOT arm auto-merge yet — Task 6's bench must confirm byte-identical clusters + wall first.

---

## Task 6: Remote bench — pin threshold, prove byte-identical clusters + wall

**Files:** none (uses `.github/workflows/bench-zero-config.yml`, unchanged)

**Background:** All at-scale verification is remote (local OOMs). The #1680 green baseline (run 29162502432): median wall **455.67s**, `cluster_count=434,572`, `multi_member_cluster_count=59,242`. The realistic wall floor is ~270-300s (scoring persists inside batches; the ~276s lock overhead + per-future `as_completed` churn is what's recoverable — see spec §Problem perf note). A landing in that region is success.

- [ ] **Step 1: Dispatch the bench on the branch (default threshold)**

```bash
gh workflow run bench-zero-config.yml --repo benseverndev-oss/goldenmatch \
  --ref feat/scorer-adaptive-block-batching \
  -f n_records=500000 -f runs=3 -f label=block-batching-default
```

Poll (do not busy-wait; check back): `gh run list --repo benseverndev-oss/goldenmatch --workflow bench-zero-config.yml --limit 3`

- [ ] **Step 2: Read the result**

```bash
gh run view <run-id> --repo benseverndev-oss/goldenmatch --log | grep -aE 'median wall|cluster_count|multi_member_cluster_count|oversized_cluster_count'
```

Assert:
- `cluster_count = 434572` AND `multi_member_cluster_count = 59242` (byte-identical clusters). If these differ, the change is WRONG — stop, diff the pair set on a small fixture, fix before proceeding.
- Median wall materially below 455s (expect ~270-320s).

- [ ] **Step 3: (If wall disappoints) sweep `_SOLO_BLOCK_MIN_PAIRS`**

If wall didn't drop as expected, dispatch 1-2 more runs varying the threshold via env in the workflow (add `-f`... or set the env in the workflow dispatch by editing the branch's workflow `env:` temporarily, OR rely on the module default). Candidate values: 2_000, 10_000, 50_000. Pick the value with the lowest wall at identical cluster counts. Pin it as the `_SOLO_BLOCK_MIN_PAIRS` default in `scorer.py` and commit:

```bash
git commit -am "perf(scorer): pin _SOLO_BLOCK_MIN_PAIRS default from bench sweep (<value>)"
git push
```

- [ ] **Step 4: Record the numbers**

Note the final (label, median wall, cluster_count, multi_member_count, threshold) in the PR description and in the work tracker. This is the perf proof + the standing correctness assertion.

---

## Task 7: Docs sweep + merge

**Files:**
- Modify: `packages/python/goldenmatch/CLAUDE.md` (the "Track 1 Fix B, deferred" perf note)
- Modify: `docs-site/goldenmatch/tuning.mdx` (the two new env tunables)

Per @superpowers rollout-docs-sweep: sweep every doc surface at the end of the rollout.

- [ ] **Step 1: Update the package CLAUDE.md perf note**

Find the `5M-on-one-node bucket` / `bucket_score is 42 min` note and the "Further opportunity: batch many small blocks per cdist call (Track 1 Fix B, deferred)" line. Replace "deferred" with a pointer that adaptive block-batching shipped in `score_blocks_parallel`/`score_blocks_columnar` (this PR), with the measured 500K wall drop and the two env tunables.

- [ ] **Step 2: Document the tunables**

Add `GOLDENMATCH_SCORER_SOLO_BLOCK_MIN_PAIRS` and `GOLDENMATCH_SCORER_BATCH_BINS_PER_WORKER` to `docs-site/goldenmatch/tuning.mdx` (the canonical runtime-config doc, per memory `reference_tuning_opt_ins_doc`), each with a one-line "what it does / when to touch it."

- [ ] **Step 3: Validate docs**

Run (from `docs-site/`): `mint broken-links` (and `mint validate` if available).

- [ ] **Step 4: Commit + arm auto-merge**

```bash
git add packages/python/goldenmatch/CLAUDE.md docs-site/goldenmatch/tuning.mdx
git commit -m "docs(scorer): adaptive block-batching shipped + tunables"
git push
gh pr merge <PR#> --auto --squash --repo benseverndev-oss/goldenmatch
```

Per memory `feedback_dont_poll_ci_arm_automerge`: arm `--auto --squash` and STOP; do not poll CI. Per `reference_branch_protection_strict_up_to_date`: main is a native merge queue requiring only `ci-required` — enqueue and stop.

- [ ] **Step 5: Worktree cleanup (after merge lands)**

```bash
cd /d/show_case/goldenmatch
git worktree remove /d/show_case/gm-block-batching
```

---

## Definition of Done

- `BlockResult.n_rows` populated on all cheap construction paths; `None`-safe planner.
- `_plan_block_batches` unit-tested (solo/binned/None/mixed/empty).
- `score_blocks_parallel` + `score_blocks_columnar` submit per-batch; byte-identical pair set proven by equivalence unit tests.
- `bench-zero-config` @ 500K: `cluster_count=434572`, `multi_member_cluster_count=59242` (unchanged), median wall materially below 455s.
- `_SOLO_BLOCK_MIN_PAIRS` default pinned from the sweep (not the placeholder).
- Docs swept (CLAUDE.md perf note, tuning.mdx tunables).
- PR merged via queue; worktree removed.
