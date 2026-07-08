# Fused Arrow-native match kernel — design

**Date:** 2026-07-08 • **Status:** Design (approved direction; committed to the thesis) •
**Goal:** a compilable Arrow-native **match stage** — Arrow columns in → block + score +
dedup + cluster all in Rust → Arrow clusters out, **one FFI crossing, zero intermediate
Polars** — so GoldenPipe can thread the match stage into a native cross-package pipeline.

## Why this shape (and why the prior work didn't get here)

GoldenMatch is already further along than GoldenFlow was: pyo3-free source-of-truth cores
(score/graph/fingerprint/sketch/…), native-authoritative hot path under
`GOLDENMATCH_NATIVE=auto`, and **real Arrow-C-Data zero-copy kernels already exist** for
scoring (`score_block_pairs_arrow`), dedup (`dedup_pairs_arrow`, 10.6×), and clustering
(`build_clusters_arrow`). What's missing is not the kernels — it's a path that runs them
back-to-back **without a Polars/pyarrow conversion between each stage**.

The prior attempts (the "Arrow everywhere" spike, the columnar pipeline, the DataFusion
one-box spine) were measured as a wash/NO-GO. **The honest read of *why*: they converted at
every stage boundary.** The spike's ~3% ceiling was the per-boundary Polars↔Arrow↔native
conversion overhead eating the kernel wins; the columnar pipeline is 1–2% slower + 13–16%
RSS because it still threads Polars frames between stages. None of them did the match stage
as **one fused Rust call**. That is the untried lever, and it's exactly what made
GoldenFlow's `transform_csv` (whole file→transform→file in ONE Rust call) a clean win: no
intermediate materialization means no conversion ceiling.

So: **one native function, `match_fused(arrow_columns, config) → arrow_clusters`**, that
orchestrates block→score→dedup→cluster in Rust. Covered configs run fully native; the exotic
tail declines to the existing Python pipeline (the GoldenFlow columnar-decline pattern).

## The one new kernel: block formation

Everything downstream is already a native Arrow kernel. The only genuinely-new piece:

1. **Block-key derivation.** Per-field transform chains (`lowercase`/`strip`/`substring`
   native; `soundex`/`double_metaphone` are already score-core/goldenflow-core kernels),
   concatenated with `||` (single field = the field itself). All the transforms already
   exist in Rust — this is a chain-apply over Arrow Utf8, producing a key column.
2. **Hash-group-by.** `hash(block_key) % N → bucket`, group by `__block_key__` within
   bucket (the `score_buckets.py` partition). This is THE Polars-compute holdout — a hash
   group-by over an Arrow string column. It's the load-bearing new kernel and the one thing
   benchmarked head-to-head against Polars' SIMD `group_by`/`partition_by`.
3. **Intra-block candidate pairs.** Within each block, the O(k²) pair set feeds the existing
   `score_block_pairs_arrow`.

`select_best_blocking_key` (size-distribution analysis) can stay Python orchestration — it's
a one-shot decision, not the hot loop; the fused kernel takes the CHOSEN key.

## Covered vs declined (the boundary)

**Covered (fused native):** deterministic blocking on a field-transform key, exact +
rapidfuzz-family scoring (jaro_winkler/levenshtein/token_sort/exact) with weighted matchkeys
above a threshold, Union-Find/WCC clustering. This is the bucket+native production path —
already the measured winner — just fused end to end.

**Declined to the Python pipeline (unchanged):** ANN sub-blocking (HNSW) for oversized
blocks, probabilistic (Fellegi-Sunter) scoring, domain feature extraction, negative-evidence
post-filters, PPRL, LLM/rerank/boost, correlated survivorship. `config_is_match_fused_ready`
gates it (the `config_is_columnar_ready` analog); anything uncovered runs the existing
pipeline, so behavior is never wrong — coverage grows over increments.

## Byte-parity contract (load-bearing)

The kernel MUST be byte-identical to the current Python pipeline on a covered config:
- **Candidate pairs** identical to `build_blocks` + the exact self-join (same block-key
  derivation, same intra-block pair set, same exact-match pairs).
- **Scores** identical (same score-core kernels the bucket path already uses — identical by
  construction).
- **Clusters** identical to `build_clusters_arrow` (same graph-core WCC).
Gated by a parity harness: `match_fused(arrow) == dedupe_df(pl.DataFrame)` on covered
configs over a corpus (Febrl/NCVR + synthetic), pairs + clusters + golden.

## Increments (each shippable, parity-gated, measured)

1. **Native block-formation kernel** (this is the load-bearing new piece): key-derive
   (existing transform kernels) + hash-group-by + intra-block pairs, Arrow-in → Arrow
   candidate-pair buffer out. Byte-parity with `build_blocks`; **benchmark vs Polars
   `group_by` at 1M/5M** (the thing that killed the spike — if it can't at least match
   Polars' SIMD group-by, the fused win must come from eliminated conversions, measured
   honestly, not assumed).
2. **Fuse block→score→dedup→cluster** into `match_fused` (one FFI call), covered boundary +
   `config_is_match_fused_ready`. Measure the WHOLE stage vs the current pipeline — the
   thesis stands or falls on the fused number beating the per-stage baseline.
3. **Arrow-native public entry** (`dedupe` accepting a dict-of-columns / Arrow table,
   returning Arrow), so GoldenPipe threads the stage with no `pl.DataFrame` at the boundary.
4. **GoldenPipe integration**: the planner emits an Arrow plan; flow→match hands off Arrow
   record-batches with no re-materialization (the cross-package composability payoff).

## Honesty guardrails (per the arc's own lessons)

- **Measure the WALL at scale before believing it** (the perf-audit + #715 lessons). The
  spike/columnar verdicts are the baseline to BEAT, not ignore. If increment 1's group-by is
  slower than Polars AND the fused increment-2 number doesn't beat the pipeline, the honest
  verdict is the same NO-GO the prior work reached — and we say so, not ship motion.
- **No output change ever** (byte-parity, gated). Covered path only; decline loudly.
- The value case is **composability for compiled cross-package planning**, not necessarily a
  single-stage speed win. Increment 4 is where that pays; 1–3 are the substrate for it.
