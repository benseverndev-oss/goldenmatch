# GoldenMatch Polars eviction — W2c plan (columnar spine port)

Parent plan: `docs/superpowers/plans/2026-07-10-goldenmatch-polars-eviction-w2.md`
(batch table). Predecessors merged: W0 #1616, W1 #1622, W2a #1632, W2b #1633.
Spec: `docs/superpowers/specs/2026-07-09-goldenmatch-polars-eviction-design.md`.

## Goal

Port the columnar pair-stream spine onto the Frame seam so the SAME code runs
on either backend: `_find_exact_match_ids` + the columnar scorer emit/filter
paths (scorer.py), `build_cluster_frames` + `_columnar_presplit` + the
native-arrow bridge (cluster.py), `_multi_df_from_frames` +
`build_golden_records_from_frames` routing (golden.py), and the pipeline's
frame threading between them.

## Non-goals / invariants (recon-verified 2026-07-10)

- **Default behavior changes: NONE.** `GOLDENMATCH_FRAME` stays `polars`
  (every seam op delegates byte-identically); `GOLDENMATCH_COLUMNAR_PIPELINE`
  stays `0` (the do-NOT-flip verdict stands: ~1-2% slower / +13-16% RSS).
  W2c is dual-backend routing only.
- **End-to-end arrow through the engine is W2d**, not W2c: `load_file` still
  converts to Polars at the ingest boundary, so in arrow mode the engine
  internals continue to receive Polars frames until W2d. W2c's arrow backend
  is exercised at the UNIT level (fixtures + arrow twins constructing
  ArrowFrames directly), and that is stated honestly in the PR.
- The golden fast/slow builders (`build_golden_records_df`,
  `build_golden_records_batch`) and everything below them stay raw
  Polars/list — W2e's expression-tail territory.
- No new Rust kernels unless the exit bench shows >10% on a ported op.

## New seam ops (fixtures-first, same W2b discipline)

Mechanical ops (Polars impl = the exact engine call, Arrow impl = pc twin):

| Op | Polars semantics | Call site |
| --- | --- | --- |
| `Frame.select(cols)` | keep-subset projection (inverse of `drop`) | pipeline:2778 is the ONLY standalone site (reviewer fix: scorer:379 is the lazy collect that stays raw Polars until W2d; golden:1296 lives inside `select_eligible_clusters`) |
| `Frame.filter_eq(col, value)` | `.filter(pl.col(c) == v)` | cluster:651 |
| `Frame.filter_not_in(col, values)` | `.filter(~col.is_in(values))` | cluster:706-707 |
| `Frame.filter_ne_cols(a, b)` | `.filter(col(a) != col(b))` — null -> mask null -> row DROPS. Pin = parity with the COLUMNAR Polars engine. NOTE (reviewer): the list path's `dict.get(a) != dict.get(b)` would KEEP a one-sided-unknown pair — a pre-existing list-vs-columnar divergence, unreachable in-pipeline because `source_lookup` is total. Do NOT "fix" the drop into a keep; the arrow twin's oracle is the columnar Polars path, not the list path | scorer:1381/1850 |
| `Frame.filter_nonblank_key(col)` | drop null + `cast(Utf8, strict=False).strip()==""` — the DQbench-T3 blank-exclusion. The `strict=False` cast is part of the contract (reviewer fix): non-string mk columns stringify first, and un-castable values become null -> `!=` null -> row DROPS. Fixture corpus includes a non-Utf8 column + an uncastable case. OPPOSITE of `filter_valid_key` re `""` -> its own named op | scorer:385-388 |
| `Frame.filter_target_split(a, b, values)` | `col(a).is_in(v) != col(b).is_in(v)` (XOR) as ONE semantic op — raw is_in+xor masks stay off the protocol | scorer:1988 |
| `Frame.with_fill_null(col, value)` | `.with_columns(col.fill_null(v))` | cluster:589-592 |
| `Frame.map_column(src, dst, mapping)` | `.with_columns(col(src).replace_strict(mapping).alias(dst))` — replace_strict RAISES on unmapped values and takes dtype from the mapping; the arrow twin must raise too, not null-fill (reviewer fix; fixture pins the raise) | cluster:1151-1152 |
| `frame_from_rows(rows, schema)` | `pl.DataFrame(rows, schema, orient="row")`. Accepts BOTH tuple rows (scorer:1735) and dict rows (cluster:711-712 passes list-of-dicts — reviewer fix). Explicit string schema required; where the raw site infers (cluster:709-710 names-only schema), the port asserts the explicit dtypes match | scorer:1735, cluster:708-714 |
| `concat_columns([cols])` | `pl.concat([s1, s2])` (Column concat; `.unique()` composes) | cluster:1938-1940 |

Semantic ops (whole intent as one op — the when/then block must NOT leak an
expression surface through the seam, per spec 4.1):

| Op | Replaces |
| --- | --- |
| `apply_weak_quality(metadata, weak_threshold)` | cluster.py:718-729's two `with_columns(when/then/otherwise)` passes (quality recompute + 0.7 confidence damp, strict `>` on threshold). Polars impl = that exact expression; Arrow impl = pc.if_else chain. Two reviewer-named traps: (a) the split branch's `.then()` carries the existing quality COLUMN through, not a literal; (b) Polars `when()` treats a NULL condition as false (falls through) while `pc.if_else` emits null — unreachable in-engine (both presplit paths coalesce min/avg to 0.0 first) but the null-condition fixture row pins the fall-through behavior explicitly. |
| `select_eligible_clusters(metadata)` | golden.py:1293-1297's `(size > 1) & ~oversized` -> `select("cluster_id")` (parenthesization is load-bearing — the raw `&`-precedence trap noted in-code). |

Shared schema constants: backend-neutral string-schema dicts
`PAIR_STREAM_SCHEMA_SPEC = {"id_a": "int64", "id_b": "int64", "score": "float64"}`
and the 9-col `CLUSTER_METADATA_SCHEMA_SPEC` live in `frame.py`;
`scorer._pair_stream_schema()` derives its Polars dict from the spec (single
source of truth; the PEP-562 lazy export is unchanged).

## Call-site port (per file)

1. **scorer.py** — `_find_exact_match_ids`: eager select -> `to_frame` ->
   `filter_nonblank_key` -> `self_join_on` -> `column(...).to_numpy()` (the
   lazy `.select().collect()` stays caller-side Polars until W2d).
   `_emit_empty`/`pairs_list_to_df` -> `empty_frame`/`frame_from_rows`.
   Both cross-source filters (the byte-identical twins at 1364-1384 and
   1801-1854 — port BOTH or neither, they are mirror-flagged) ->
   `frame_from_columns` + `rename` + `join_left` + `filter_ne_cols` + `drop`.
   `score_blocks_columnar` empties/concat -> `empty_frame`/`concat_frames`;
   `_filter_target_ids_df` -> `filter_target_split`.
2. **cluster.py** — `build_cluster_frames`: buffer-fill constructions ->
   `frame_from_columns`; oversized/auto-split filters -> `filter_mask`/
   `filter_eq`/`filter_not_in` + `concat_frames`+`frame_from_rows`; the Step-3
   quality block -> `apply_weak_quality`; native branch fill-nulls ->
   `with_fill_null`. `_columnar_presplit` -> `map_column` + covered to_list
   ops. Arrow bridge: all_ids via `concat_columns(...).unique()`, kernel
   outputs -> `frame_from_columns` (accepts pa.Array already).
   `ClusterFrames` fields: keep `pl.DataFrame` typing for now — internals
   wrap via `to_frame()` and return `.native`, so the dataclass contract
   (and every downstream consumer) is untouched on the default backend.
3. **golden.py** — `_multi_df_from_frames` -> `select_eligible_clusters` +
   `join_inner(on)` + `rename` + `join_inner(left_on/right_on)`;
   `build_golden_records_from_frames` height/columns checks -> seam.
4. **pipeline.py** — `_golden_source` projection -> `select`; the
   `_columnar_pairs_df` and `ClusterFrames` threading keeps native frames
   (wrap-at-use), so stage signatures don't change in W2c.

## Tests

- The five spine parity files stay green UNEDITED:
  `test_pair_stream_columnar_parity.py`, `test_cluster_columnar_parity.py`,
  `test_cluster_frames_out_parity.py`, `test_golden_from_frames_parity.py`,
  `test_columnar_pipeline_parity.py` (they pin byte-parity vs the list/dict
  predecessors — the port must be invisible to them).
- New `tests/test_frame_relational_ops.py` sections for every new op
  (delegation parity vs the raw snippet + cross-backend canonical equality),
  fixtures BEFORE Arrow impls.
- New arrow twins: unit tests constructing ArrowFrames for
  `apply_weak_quality`, `_find_exact_match_ids`'s seam pieces, the
  cross-source filter, and `map_column` — proving the arrow lane at the op
  level (e2e arrives with W2d).
- Full goldenmatch shards + heavy + fallback + differential lane green.

## Exit gate (before merge)

- Dispatch `bench-zero-config.yml` at 1M on the branch (via
  `gh workflow run --ref <branch>`; reviewer-verified dispatchable) vs latest
  main baseline, DEFAULT backend: wall within noise. The exact-match
  self-join is the named suspect from spec section 6 — with
  `GOLDENMATCH_FRAME=polars` the op delegates to the identical Polars join,
  so the expected delta is zero; the bench PROVES the wrapper overhead is nil
  rather than assuming it.
- **Stated deviation from the parent plan** (reviewer-required): the parent's
  W2c row says 1M "on both backends"; W2c runs the default backend only. An
  arrow-backend 1M e2e is meaningless before W2d (ingest still converts to
  Polars at the boundary, and the workflow has no `GOLDENMATCH_FRAME` input).
  The both-backends 1M moves to W2d's exit gate; the parent plan's W2c/W2d
  rows are annotated accordingly in this PR. The parent's
  "`_columnar_pipeline_enabled` becomes the seam insertion point" wording is
  likewise corrected there (the flag stays 0; W2c only ports call sites).
- `throughput-gate` green (cost-based; candidates unchanged).

## Risks

| Risk | Mitigation |
| --- | --- |
| Wrapper overhead on hot per-block loops (cross-source filter runs per block) | Wrappers are `__slots__` objects delegating one call; 1M bench is the proof; >10% -> inline the Polars call behind `resolve_frame_backend()` branch instead of wrapping |
| The mirror-flagged twin filters drift | Port both in one commit; the parity tests cover both entries |
| `apply_weak_quality` arrow impl diverges on null edges | Fixture rows with null min/avg_edge pinned first |
| `frame_from_rows` dtype inference vs explicit schema | Schema REQUIRED (no inference), string vocabulary |
