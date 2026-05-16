# Post-controller full-df perf — Attacks A & B

> **⚠ SUPERSEDED 2026-05-15.** Empirically obsolete. Post-#239 cProfile at 100K shows neither hotspot (`LazyFrame.collect` 462K calls; `builtins.max` 55M calls) appears in the top 30 cumtime. PR #239 (`perf(zero-config): 6x at 100K`) eliminated both via golden vectorize + adaptive blocking. **Do not implement Attacks A or B.** The pre-#239 487s baseline this spec was framed against was measuring stale site-packages code, not the worktree.
>
> Successor: [`2026-05-15-map-elements-attack-design.md`](2026-05-15-map-elements-attack-design.md) targets the new dominant hotspot (`PySeries.map_elements`, 15.8s cumtime per dedupe, 542 calls at 100K).
>
> The methodology in this spec is preserved as historical record. "Rank by measured wall, not static structure" was the right rule — applied to fresh data, it would have steered us at `map_elements` from the start. This spec's value is the cross-cutting-primitives lesson, not the specific attacks.

**Status:** Superseded 2026-05-15 (originally: Design, approved by user)
**Author:** brainstorm session, Claude + bsevern
**Scope:** `packages/python/goldenmatch/goldenmatch/core/learned_blocking.py`, `packages/python/goldenmatch/goldenmatch/core/cluster.py`, plus minimal call-site updates in `core/blocker.py` and `core/scorer.py` to consume the new `BlockResult` shape.
**Related:**
- Auto-config controller spec: `docs/superpowers/specs/2026-05-06-autoconfig-introspective-controller-design.md` — the controller itself is not modified; this spec attacks the full-df pipeline that runs *after* the controller commits.
- 5M scale-audit spec: `docs/superpowers/specs/2026-05-13-scale-audit-5m.md` — the chunked/DuckDB lane has separate optimizations (#231-#235); this spec targets the default polars-direct backend that `gm.dedupe_df(big_df)` invokes when the caller passes no `backend` override.

## Problem

`gm.dedupe_df(df)` zero-config on a 500K synthetic person dataset takes a median **487.7s** wall (5 runs: 441-548s, 22% spread). The dominant cost is *not* a single pipeline stage but two cross-cutting Python hotspots that span blocking, scoring, and clustering.

The CLAUDE.md performance-audit lesson is explicit on this: rank by measured wall, not static structure. Earlier hypotheses (`precompute_matchkey_transforms`, `phone_e164`, `build_learned_blocks` *as a unit*) turned out to be partial — `build_learned_blocks` is hot, but the cost is concentrated in one sub-call (`apply_learned_blocks`) whose dominant primitive (`LazyFrame.collect`) also fires from scoring.

### Measurement (500K, polars-direct backend, this laptop)

5-run median wall **487.7s**, single cProfile run wall **1213.6s** (cProfile overhead ~2.5×). Numbers below are cProfile cumtime % of the cProfile run.

#### By stage (cumtime)

| Stage | cumtime | % wall |
|---|---|---|
| `_build_learned_blocks` (→ `apply_learned_blocks` 230.9s) | 295.5s | **24.4%** |
| `score_blocks_parallel` (→ `_score_one_block`) | 279.1s | **23.0%** |
| `build_clusters` (→ `split_oversized_cluster`) | 269.4s | **22.2%** |
| `run_transform` (GoldenFlow) | 69.1s | 5.7% |

No single stage clears 30%. Diffuse: blocking ≈ scoring ≈ clustering.

#### By cross-stage primitive (tottime)

| Primitive | ncalls | tottime | % wall |
|---|---|---|---|
| `LazyFrame.collect` | 462,061 | 326.8s | **27%** |
| `builtins.max` (in `split_oversized_cluster` / `compute_cluster_confidence`) | 55,312,521 | 155.9s | **13%** |

**~40% of wall sits in these two primitives.** They cross stage boundaries because both are caused by per-block / per-edge Python loops that materialize LazyFrames or call `max()`/`min()` on dict values.

### Source artifacts

- `.profile_tmp/bench_500k_zero_config.json` — full bench output (medians, stages, cProfile top 30).
- `.profile_tmp/bench_500k_zero_config.prof` — cProfile dump (snakeviz / pstats friendly).
- `scripts/bench_1m_zero_config.py` — the bench harness (5-run median wall + per-stage timing + cProfile pass).
- `scripts/scale_audit_5m.py` — patched 2026-05-15 to use `_skip_finalize=True` and avoid the controller-finalize-plus-caller double-run that was inflating the prior cloud measurement (1237s) by ~2×.

### Caveats from the bench

1. **Stage monkey-patching mostly missed.** `setattr(matchkey, "compute_matchkeys", ...)` only rebinds the source module; `pipeline.py` does `from X import Y` and keeps the original reference. cProfile cumtime carried the day. Bench-harness fix is filed under §Out of scope.
2. **Controller commits RED on this fixture.** Every run hit `iter=v0, stop_reason=POLICY_SATISFIED, failing_subprofile=scoring`. The synthetic dataset has a scoring pathology the controller can't refit. The bench still measures the production caller path correctly, but the config under test is the heuristic v0, not a refit config. Real customer data may exercise different downstream paths.
3. **GoldenCheck fires 3× per `dedupe_df`.** Twice during auto-config (sample iteration + one extra), once during the caller pass. Quality scan dominates ~5-6% of wall. Quality is disabled-able via `config.quality.mode="disabled"` and is *not* in scope for this spec.
4. **Wall variance 22%.** Likely `ThreadPoolExecutor` scheduling in `score_blocks_parallel` + GC pressure from the 462K LazyFrames. Attack A should narrow this naturally by killing the LazyFrame churn.
5. **The recent chunked / DuckDB optimization wave (#231-#235) does not help here.** Those PRs target `backend="chunked"` and `backend="duckdb"`. The default polars-direct path that real `gm.dedupe_df(df)` callers use was untouched.

## Goals

1. **Cut polars-direct full-df wall by ≥40% at 500K** without changing config shape, output, or quality.
2. **Land the wins in two small PRs**, each independently measurable. Attack A primary; Attack B follow-up.
3. **Preserve correctness invariants** — same blocking output, same cluster partitioning, same F1 against ground truth (within 0.005 tolerance for non-determinism in scoring).
4. **Re-measure honestly** — the headline number for this spec is the **post-A+B 1M median wall** on the same laptop, captured in `.profile_tmp/bench_1m_zero_config_after.json`.

## Non-goals (v1)

- Stages other than blocking + clustering. Scoring's `score_blocks_parallel` is hot but the cost is mostly inside `rapidfuzz.cdist` (GIL-releasing C code), not the Python orchestration we can touch here. Separate audit.
- Chunked / DuckDB backends — already optimized in #231-#235 and have separate measurement lanes.
- 5M cloud re-measurement — that's the 5M scale-audit spec's problem; this spec lands wins for the default backend that the 1M lane consumes.
- Bench-harness improvements (stage-patch fix, controller-RED-on-synthetic investigation) — folded into a small follow-up.
- Controller refit policy improvements (the synthetic fixture's RED is a real signal; out of scope for the perf attack).

## Attack A — `LazyFrame.collect` explosion in `apply_learned_blocks`

### Diagnosis

In `core/learned_blocking.py`, the current implementation produces a `BlockResult` per learned block by wrapping a slice of the materialized DataFrame back into a `LazyFrame`, then collects each block once more during the dedup-by-member-set pass:

```python
# learned_blocking.py:312
block_lf = df[sorted(member_positions)].lazy()             # per-block lazy wrap (1 of 2)
all_blocks.append(BlockResult(df=block_lf, ...))

# learned_blocking.py:323
for block in all_blocks:
    block_df = block.df.collect()                          # per-block collect to dedupe
    members = frozenset(block_df["__row_id__"].to_list())
```

For each block produced, the lazy() wrap + downstream `.collect()` round-trip costs Polars' lazy-engine setup twice. `score_blocks_parallel` then calls `block.df.collect()` *again* inside `_score_one_block` to materialize the slice for rapidfuzz. That's three Polars round-trips per block, on already-in-memory positional data.

At 500K rows the bench shows **462,061 `LazyFrame.collect` calls** — roughly one per row, which matches "~60-100K blocks × 3-4 collects each."

### Fix

Replace `BlockResult.df: LazyFrame` with a positional contract: store `member_positions: list[int]` (or `np.ndarray[int64]`) and materialize the slice on demand at the single consumer (`_score_one_block`) using `df[positions]` — already an in-memory operation, no lazy round-trip.

Concretely:

1. **`BlockResult`** in `core/blocker.py`:
   - Add `member_positions: list[int] | None = None`.
   - Keep `df` field for backward compat (deprecation path; downstream code that reads `.df` continues to work for one release).
   - Add a `materialize(parent_df: pl.DataFrame) -> pl.DataFrame` helper that does `parent_df[self.member_positions]` if positions are present, else falls back to `self.df.collect()`.

2. **`apply_learned_blocks`** in `core/learned_blocking.py`:
   - Build `BlockResult(member_positions=sorted(member_positions), df=None, ...)` instead of wrapping in `LazyFrame`.
   - Dedupe blocks by `tuple(sorted(member_positions))` *before* constructing `BlockResult`. Eliminates the second collect pass entirely.
   - Pass the parent `df` reference downstream (one shared `pl.DataFrame`, not per-block).

3. **`score_blocks_parallel` / `_score_one_block`** in `core/scorer.py`:
   - Accept the parent `df` as an argument.
   - Replace `block.df.collect()` with `block.materialize(parent_df)`.

4. **`build_blocks`** in `core/blocker.py`:
   - Static / multi-pass blocking paths also wrap slices in `LazyFrame`. Apply the same positional pattern. Smaller share of the 462K collects, but folding it in keeps the contract consistent.

### Expected reduction

- 462,061 collects → 1 per block (single materialize during scoring) × ~60-100K blocks = ~70-100K collects, of which scoring's are already needed for rapidfuzz.
- Net: ~250-280s off wall at 500K (~51-57% of `apply_learned_blocks` cumtime).
- Wall variance should narrow: fewer LazyFrame objects means less GC pressure and less Polars-internal threadpool interference.

### Risk and mitigation

- **`BlockResult.df` is a public-ish interface.** Any external consumer reading `.df` keeps working via the fallback path during the deprecation window. Single release later, drop the fallback.
- **Position-vs-`__row_id__` confusion.** `apply_learned_blocks` already uses positions (see comment at `learned_blocking.py:297-300` from the prior optimization). Positions track df ordering — same invariant the function already relies on.
- **Multi-pass blocking writes blocks from multiple `build_blocks` calls into one list.** All passes share the same parent `df` because the pipeline materializes once at `precompute_matchkey_transforms`. Spec assumes this; verify in implementation.
- **`score_blocks_parallel` worker function signature changes.** It's an internal helper — call sites are `pipeline.py`, `engine.py`, `chunked.py`. All three updated atomically.

### Acceptance for Attack A

- Re-run `scripts/bench_1m_zero_config.py` at 500K. Median wall ≤ 250s (was 488s; target ≥ 49% reduction, ≥ 200s absolute).
- `LazyFrame.collect` ncalls in cProfile drops to < 50,000.
- F1 against the synthetic ground truth within 0.005 of pre-A baseline.
- All existing tests in `tests/test_blocking.py`, `tests/test_scorer.py`, `tests/test_chunked.py` pass without modification.

## Attack B — `split_oversized_cluster` vectorization

### Diagnosis

`core/cluster.py::_build_mst` runs Kruskal's algorithm in pure Python over `pair_scores` items, picking the weakest MST edge via `min(mst, key=lambda e: e[2])`. `compute_cluster_confidence` iterates the same dict per sub-cluster to compute min/avg edges and bottleneck pair. With 38,315 split iterations on the bench fixture, the inner Python work multiplies out.

The bench measures **55,312,521 `builtins.max` calls** (we also see the symmetric `min` cost — pstats groups them differently but the algorithmic shape is the same). At 13% of wall this is the second-largest cross-stage primitive after the LazyFrame churn.

### Fix

Vectorize the inner loop with NumPy:

1. **Pack pair_scores once per split:**
   ```python
   edges_uv = np.fromiter(((a, b) for (a, b) in pair_scores), dtype=np.int64, count=len(pair_scores) * 2).reshape(-1, 2)
   weights = np.fromiter(pair_scores.values(), dtype=np.float64, count=len(pair_scores))
   ```

2. **Sort by weight descending once:**
   ```python
   order = np.argsort(-weights, kind="stable")
   edges_uv = edges_uv[order]
   weights = weights[order]
   ```

3. **Run Kruskal with the existing `UnionFind` but iterate over the sorted NumPy arrays directly** — eliminates the per-edge tuple unpacking that drives most of the Python-side overhead.

4. **Weakest MST edge** becomes `mst_weights.argmin()` → `weakest_idx`; no Python `min(..., key=...)`.

5. **`compute_cluster_confidence`:** take the cluster's edge subset as a NumPy view (`weights[mask]`), compute `min()`, `mean()`, `len()` natively. Bottleneck pair: `edges_uv[mask][weights[mask].argmin()]`.

The UnionFind itself stays Python — it's hot but tiny (≤ cluster_size operations per edge). If profiling shows it remaining as the bottleneck after the vectorization, swap to a NumPy-backed UF in a follow-up.

### Expected reduction

- ~13% wall → ≤ 2% wall (NumPy ops vs 55M Python ops).
- Net: ~80-100s off wall at 500K.
- Combined with Attack A, target post-A+B 500K median ≈ 150s.

### Risk and mitigation

- **Algorithmic correctness must match exactly.** Existing `tests/test_cluster.py` covers split correctness on hand-crafted oversized clusters. Plus: add a Hypothesis property test asserting `split_oversized_cluster_vectorized(members, pair_scores) == split_oversized_cluster_python(members, pair_scores)` for random graphs ≤ 50 nodes.
- **`pair_scores` key canonicalization.** The codebase invariant is `(min(a,b), max(a,b))` (per `packages/python/goldenmatch/CLAUDE.md`). NumPy pack code must preserve that — pack via `(min, max)` regardless of dict key order.
- **Float precision.** NumPy float64 matches Python float in IEEE 754; sort stability via `kind="stable"` preserves deterministic ordering on equal weights.

### Acceptance for Attack B

- Re-run bench. Median wall ≤ 150s (target ≥ 60s additional absolute reduction beyond A).
- `builtins.max` cumtime in cProfile drops below 30s (was 156s, expecting an order of magnitude).
- All `tests/test_cluster.py` tests pass without modification.
- Hypothesis property test (new) passes for ≥1000 random inputs.

## Combined target table

| Lane | Today | Post-A | Post-A+B |
|---|---|---|---|
| 500K median wall (this laptop) | 487.7s | ≤ 250s | ≤ 150s |
| 1M median wall (linear extrapolation, ceiling) | ~16 min | ~8 min | ~5 min |
| `LazyFrame.collect` ncalls | 462,061 | < 50,000 | < 50,000 |
| `builtins.max` ncalls (cluster path) | 55,312,521 | unchanged | < 5,000,000 |

The 1M numbers are linear projections from 500K. Clustering can be super-linear in pathological cases; final exit gate is the measured 1M run after both attacks land, **not** the projection.

## Testing

### Tier 1 — Existing unit tests (no modification)

`tests/test_blocking.py`, `tests/test_learned_blocking.py`, `tests/test_scorer.py`, `tests/test_chunked.py`, `tests/test_cluster.py` all pass unchanged. Any test that needs to access `BlockResult.df` exercises the deprecation-fallback path.

### Tier 2 — Correctness invariants (new)

- **Block content equality:** `apply_learned_blocks(df, rules)` produces the same `__row_id__` member sets before and after Attack A. Compare by hashing each block's frozenset of row_ids; assert equal multisets.
- **Cluster partition equality:** `split_oversized_cluster_vectorized` returns the same partition as the Python implementation on hand-crafted fixtures. Drop the old implementation only after at least one full release cycle.
- **F1 invariance:** running the synthetic 500K fixture end-to-end before and after each attack produces F1 within 0.005. Tolerance accounts for `ThreadPoolExecutor` non-determinism in pair ordering, which can rarely swap pair_scores ties.

### Tier 3 — Property tests (new, Hypothesis)

- `split_oversized_cluster` python ≡ numpy on random graphs (≤ 50 nodes, edge density 0.3-0.9, random weights).
- `BlockResult.materialize(parent_df)` returns a DataFrame whose `__row_id__` column matches `member_positions` translated through `parent_df`.

### Tier 4 — Performance bench (gating)

`scripts/bench_1m_zero_config.py` re-run after each attack. Exit gates per attack:
- Attack A: 500K median ≤ 250s AND `LazyFrame.collect` ncalls < 50,000.
- Attack B: 500K median ≤ 150s AND `builtins.max` cumtime < 30s.
- Combined (after both land): 1M median ≤ 8 min on this laptop, captured in `.profile_tmp/bench_1m_zero_config_after.json`.

### Tier 5 — CI scale-audit (sanity)

The existing scale-audit workflow (`.github/workflows/scale-audit-5m.yml` and the 1M cloud runs) re-runs on `ubuntu-latest-large`. Numbers won't be directly comparable to laptop (different hardware) but the qualitative shape — 500K and 1M both fitting in memory, F1 unchanged — should hold.

## Implementation order

1. Land Attack A as a single PR. Include the `BlockResult.member_positions` field, `apply_learned_blocks` rewrite, `_score_one_block` consumer update, and the block-content correctness test.
2. Re-measure at 500K. Confirm Attack A gate. Land before starting B.
3. Land Attack B as a separate PR. Include the NumPy vectorization, Hypothesis property test, cluster partition correctness test.
4. Re-measure at 500K. Confirm Attack B gate.
5. Run the 1M bench. Capture `.profile_tmp/bench_1m_zero_config_after.json`. Update CHANGELOG with the measured delta.
6. Memory entry under `project_post_controller_full_df_perf.md` — capture what surprised us (e.g. "stages were diffuse, but two cross-stage primitives owned 40%; LazyFrame churn was the bigger fish; future audits should grep cProfile by primitive, not just stage").

## Out of scope (v1) / Future work

- **Bench-harness stage-patch fix.** Patch at `pipeline.py` call sites or use `unittest.mock.patch.object` with `wraps=`. Folded into the implementation plan's tooling task, not a blocker.
- **GoldenCheck triple-fire.** Quality scan runs three times per `dedupe_df` call. Probably a controller-internal duplicate. Separate audit; ~5-6% wall at 500K.
- **`score_blocks_parallel` Python orchestration.** Rapidfuzz `cdist` is C-level GIL-releasing — the residual Python cost is in pair-emission and threadpool dispatch. Smaller surface; revisit after A+B land.
- **Controller RED commit on synthetic fixture.** The synthetic person dataset has a scoring pathology the v1 heuristic policy can't refit out of. Could be a synthetic-generator issue, could be a real policy gap. Out of scope here; flagged for the controller v1.13 cycle.
- **`backend="chunked"` and `backend="duckdb"` parity.** Both have their own optimization arc (#231-#235). Apply analogous lazy-collapse + cluster-vectorization patterns if profiling shows the same hotspots at 5M+; out of scope for v1 of this spec.

## Open questions / things to validate during implementation

1. **Will the deprecation shim on `BlockResult.df` slow Attack A?** If keeping the fallback adds branching cost inside the hot scorer loop, drop it and update consumers atomically. Decide during implementation; measure both ways.
2. **Multi-pass `build_blocks` (static + soundex) call shape.** Spec assumes a single parent `df` reference flows through. If passes are constructed against different transformed frames, the contract needs refinement.
3. **NumPy vs Polars for the cluster vectorization.** NumPy is the safe choice (no Polars lazy-engine setup). If Polars expressions on the pair_scores frame turn out cleaner, swap during implementation — same algorithmic shape, smaller diff.
4. **Wall variance.** 22% spread at 500K is uncomfortable. If Attack A doesn't narrow it materially, dig into ThreadPoolExecutor worker count (pin to `os.cpu_count() // 2`?) before declaring victory.

## Acceptance criteria

- v1 ships when:
  1. Attack A PR merged, 500K bench median ≤ 250s, `LazyFrame.collect` ncalls < 50,000, F1 within 0.005.
  2. Attack B PR merged, 500K bench median ≤ 150s, `builtins.max` cumtime < 30s, F1 within 0.005.
  3. 1M bench re-run, median ≤ 8 min on this laptop. Result committed to `.profile_tmp/bench_1m_zero_config_after.json`.
  4. CHANGELOG entry with the measured before/after deltas. README "what's new" callout if delta > 50%.
  5. Memory entry under `project_post_controller_full_df_perf.md` capturing the cross-stage-primitive lesson.
  6. Existing test suite (1572 passing per CLAUDE.md baseline) continues to pass.
