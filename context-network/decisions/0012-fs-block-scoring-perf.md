# 0012 — FS block-scoring perf: the "native is slow" red herring + the per-block fan-out fix

**Status:** accepted (2026-06-12; PR #869, commits `187ed6a`/`85988f0`/`9142f7f`, CI green). Wall numbers are LOCAL on `historical_50k` (50,578 rows, probabilistic auto-config); the committed CI bake-off table predates these commits — see Consequences.

## Context
The ER vendor comparison ([../../docs/er-vendor-comparison.md](../../docs/er-vendor-comparison.md))
and the bake-off ([../../docs/benchmarks/2026-06-09-splink-bakeoff.md](../../docs/benchmarks/2026-06-09-splink-bakeoff.md))
claimed "Splink is 3-19x faster per node." Challenged on whether GoldenMatch's
"arrow-native, Rust-based" FS path could really be that slow, an audit found the
bake-off **never set `GOLDENMATCH_FS_NATIVE`** — and probabilistic mode (unlike
`hand_built`) doesn't refuse on a missing kernel, so it silently ran the **numpy**
path. The "3-19x" compared GM's *default numpy* auto-config against Splink's
optimized DuckDB-SQL.

A `gm_prob_native` bake-off column was added (native ext built + `score_block_pairs_fs`
symbol asserted in CI so a missing kernel fails loudly instead of degrading). Result:
**native ≈ numpy, no wall change** (historical_50k 69.1s native vs 69.1s numpy).
The FS Rust kernel doesn't help because the wall is **not** scoring arithmetic —
it is **per-block fan-out**: historical_50k produces 31,735 blocks of which **79%
are ≤8 rows** (median 4), each row in ~6 blocks (multi-pass overlap), so scoring
fanned out into ~222k tiny `score_field_matrix` calls that are FFI/marshal-bound
(~101µs/call), not compute-bound. Both kernels are already near-optimal on the math.

## Decision
Optimize the **default numpy vectorized FS path** (`score_probabilistic_vectorized`),
one measured change at a time, each gated for output-equivalence. Three landed:

1. **Value-dedup in per-field matrices** (`_field_score_matrix_dedup`, −32%). Score
   the DISTINCT values (`d×d`) and gather via an index map; the blocking-key field
   is constant within a block → 1×1. Bit-identical EXCEPT multiplicity-based
   exact/soundex matrices leave a singleton diagonal at 0 (read by equal-value
   off-diagonal pairs after dedup) — pin the unique-matrix diagonal to the true
   self-score 1.0 to restore exactness.
2. **Batch small blocks into shared per-field matrices** (`score_probabilistic_vectorized_batch`
   + `score_probabilistic_blocks_batched`, −48%, −65% cum). Coalesce blocks up to a
   row cap, compute one (deduped) S×S matrix per field, slice each block's DIAGONAL
   sub-matrix. Within-block cells are identical to per-block scoring; off-diagonal
   cross-block cells are computed and discarded. Collapses 222k native calls → 4.3k.
   Bench-dump path stays per-block (exact candidate accounting); native-FS / scalar /
   model-backed scorers fall back to per-block.
3. **Tune the batch row-cap 512 → 256** (−20%, −72% cum). The dense S×S numpy
   level/weight work grows with the cap while native calls are already cheap; a sweep
   put 256 at the knee (96..512 → 25.9/26.0/25.9/25.3/25.6/27.7s). Cap only changes
   grouping → pairs identical at any value.

### Correctness gate (load-bearing method)
The cluster-count hash is an **unreliable** gate: the pipeline is non-deterministic
run-to-run (11,542–11,545 clusters on identical code/config) because EM
training-sample order isn't fully pinned. The real gate is a **fixed-`em_result`
pair-set diff**: score every block batched-vs-per-block and assert the emitted
`(a,b,score)` set is byte-identical. All three optimizations pass it (200,058 pairs,
0 added / 0 dropped / 0 score diffs).

### Rejected
- **FS-native multi-block batching** (the kernel already takes a block-sizes list).
  Would be less code, but it's the opt-in `FS_NATIVE` path with the discrete-level
  float-boundary parity caveat (decision 0008) — not bit-identical. Numpy
  block-diagonal batching is provably exact, so it was chosen.
- **Single-`select` per block + transform memo** (replacing the per-field getitem).
  Output-identical but MEASURED a wash/slight regression (34.3s vs 30.7s) — `to_dict`
  + tuple-key cache overhead; the per-block cost is the `collect()`+gather, not the
  getitem. Reverted.

## Consequences
- **The committed CI bake-off table predates these commits.** Its `gm_probabilistic`
  walls (e.g. historical_50k 69s) are pre-optimization; the GM-vs-Splink wall ratio
  should be re-stated by re-running `bench-probabilistic.yml` (`run_bakeoff=true`) on
  the optimized branch. Until then, the "3-19x" framing in the vendor/bench docs is
  flagged as pre-optimization (and as having measured the numpy, not native, path).
- **EM-sampling nondeterminism** (±3 clusters run-to-run) is a real reproducibility
  gap, surfaced while building the gate, INDEPENDENT of this work. #829 pinned block
  *order* for the training-pair sample's F1; a residual cluster-count wobble remains.
  Flagged for a separate fix; tracked here so the next FS change uses the pair-diff
  gate, not cluster counts.
- **Next levers** (both structural, not yet done): per-block `collect()` avoidance
  (collect the combined frame once, gather members by row-id — needs `BlockResult` to
  expose membership) and candidate-pair dedup across multi-pass passes (rows recur in
  ~6 blocks).
- Knob: `GOLDENMATCH_FS_BATCH_ROWS` (default 256) tunes the batch row cap.

---
**Classification:** decision/accepted • **Last updated:** 2026-06-12
