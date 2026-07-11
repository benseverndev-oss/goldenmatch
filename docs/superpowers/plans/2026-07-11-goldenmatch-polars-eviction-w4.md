# GoldenMatch Polars eviction — W4 plan (tails)

Spec row (2026-07-09 design, §3): distributed (Ray `map_batches(batch_format="pyarrow")`),
chunked, db/identity, web, TUI engine, MCP/A2A handlers, in-repo downstream consumers →
"Polars unreferenced" in those areas. W0-W3 merged (#1616, #1622, #1632-#1655,
#1657/#1660/#1663/#1664/#1665). Post-W3 inventory: 1,556 `pl.` uses / 404 files; W4 tail
share ≈ 420 uses.

## Recon findings (2026-07-11)

### Tails (identity / db / web / tui / chunked / mcp / a2a / connectors)

1. **Dominant unmapped shape: positive `is_in(id_list)` filter** —
   `identity/resolve.py:170,918,996`, `db/sync.py:994`, `tui/engine.py:312,338,339`.
   Seam has `filter_not_in` only. NEW op `filter_in(col, values)`.
2. **IO is the dominant tail shape and stays OUT of the seam by design** — read_csv/
   scan_csv/scan_parquet/read_json/scan_ndjson across web, mcp (5×), a2a (5×),
   connectors, chunked, sync. These route through `core/ingest.py::load_file` /
   `core/io_arrow.py` (+ a write-side twin where needed), not Frame ops.
3. **LazyFrame plumbing is concentrated and stubborn**: `db/sync.py` (staging-parquet +
   weakref.finalize GC hazard #388, explicit isinstance-LazyFrame collect branch at 355),
   `chunked.py` (scan+slice streaming), `connectors/*` read contracts return LazyFrame.
   Eager-only seam cannot represent these. Strategy: keep the lazy PLUMBING but make the
   payloads backend-polymorphic at the boundaries (arrow lane goes eager via io_arrow;
   polars lane unchanged) — no seam LazyFrame.
4. **Schema-vocabulary blockers**: `identity/resolve.py` graph-bootstrap frames use
   `pl.Datetime` (753-806) — `_SCHEMA_DTYPES` has no datetime. Extend the vocabulary
   (`"datetime_us"`), pinned by fixtures. `_stitch_new_record` builds a single row
   aligned to the parent frame's LIVE dtypes (dtype fidelity is load-bearing for the
   payload hash, resolve.py:1007-1023) — needs a `row_aligned_to(frame, dict)`-shaped op
   or stays native behind an explicit backend branch.
5. **`identity/fingerprint_batch.py` is a native-dtype-lattice canonicalizer** (Binary/
   Duration/Decimal/Datetime tz+unit/List/Struct dispatch, temporal formatting, overflow
   guards). `semantic_dtype()`'s 5-tag set is far too coarse. Port = hand-written
   Arrow-native twin (`fingerprint_batch_arrow`) selected by backend, cross-pinned by the
   existing fingerprint golden vectors — NOT a seam port.
6. **Backend leakage to convert**: `web/routers/match.py:168` catches
   `pl.exceptions.ColumnNotFoundError`; `a2a/skills.py:481` isinstance-dispatches on
   `pl.DataFrame`. Convert to backend-neutral (seam exception mapping / Frame isinstance).
7. **Inference-based `pl.DataFrame(rows)` constructions** (db connectors, sync golden_df,
   a2a, tui) violate `frame_from_rows`' explicit-schema contract. Strategy: connector/IO
   boundaries construct via a new `frame_from_records(rows, backend)` op with
   inference parity pinned by fixtures (arrow: pa.Table.from_pylist), OR stay native
   where they feed straight into polars-only paths until W5.
8. Clean fits already: `web/preview.py` (int64 schema → frame_from_rows),
   `tui/boost_tab.py` (`filter_eq`), `web/runs.py` (n_unique/filter_eq),
   `identity/stitching.py` (`filter_nonblank_key` + near-`group_partitions`),
   `chunked.py` block-key derivation (== `derive_block_key`), concat accumulation
   (`concat_frames`), `db/hybrid_blocking.py` (empty-frame sentinels).

### Distributed (clustering.py 114 / identity_partition.py 27 / scoring.py 21 / pipeline.py 18 / record_store.py 11 / golden.py 11)

1. **Every Ray `map_batches`/`iter_batches` is ALREADY `batch_format="pyarrow"`** (30+
   sites verified; zero polars batch format). UDF pattern everywhere:
   `pl.from_arrow(batch)` → polars exprs → `.to_arrow()`. Port = flip UDF INTERNALS at
   the bookends; Ray plumbing untouched. No spec-feared batch-format migration needed.
2. **Memory-tuned WCC = `randomized_contraction_wcc`** (clustering.py:1281; distributed
   kernels 1115-1240). Polars is NOT the load-bearing memory holder — per-batch
   transient frames + Arrow transport + `_rc_checkpoint` (1147: ds.write_parquet +
   read_parquet, deliberate lineage truncation per 1348-1352) cap memory. Kernel port is
   memory-safe IF the checkpoint boundary + per-batch materialization are preserved.
   The 5M chain bench still gates (expected != measured).
3. **No module-level pl.* execution remains in distributed/** (function-local imports;
   dtype constants inside bodies). No lazy-import hazard.
4. **Recurring shapes → ops**: `pl.concat` symmetrize/merge/explode (clustering 885-946,
   scoring 362 `vertical_relaxed`, identity_partition 171) → module-level `concat_frames`
   EXISTS (agent missed it; scoring needs a `relaxed=True` variant); window ops
   `pl.len().over()` / `min().over()` (clustering 430, 1235) → NEW `with_group_len_over` /
   `with_group_min_over`; `pl.min_horizontal`/`max_horizontal` pair canonicalization
   (scoring 560) → NEW `with_pair_canonical` (or two horizontal ops);
   `replace_strict` row_id→cluster remap (pipeline.py:411-413 uses `default=-1` then
   filters the sentinel) → REVIEWER-RESOLVED: `map_column` is strict-raise on both
   backends (frame.py:449/938) and does NOT cover it; extend `map_column` with an
   optional `default=` (None keeps the raise contract), fixtures pin both modes.
5. **record_store bucketing** (199-279): join → `join_inner`; `partition_by` →
   `group_partitions`; polars `.hash(seed=BUCKET_HASH_SEED) % n_buckets` — per-backend
   hash acceptable for shard layout (not output-visible within a run), BUT
   REVIEWER-FOUND HAZARD: bucket dirs PERSIST and are reused
   (`GOLDENMATCH_PREPARED_RECORD_STORE_PERSIST=1` pipeline.py:3209-3217; caller-managed
   path stores, record_store.py:79-81) and `materialize_bucketed_blocks` does NOT clear
   an existing bucket_dir (mkdir exist_ok=True, record_store.py:226) while the dir key
   signature is CONFIG-ONLY (pipeline.py:846-864) — a backend flip (or data change) over
   a persisted store leaves stale bucket parquets that iter_buckets returns → stale rows
   scored. Latent pre-existing bug that per-backend hashing newly triggers. W4e MUST
   clear the bucket_dir on materialize (or fold backend+data into the signature).
   `write_parquet(compression=snappy)`/`read_parquet` shard IO → io_arrow twins.
6. **Keep as subsystems, not seam ports**: scoring's `apply_standardization(df.lazy())` /
   `compute_matchkeys(df.lazy())` collect chains (the W5 expression-stage boundary — same
   engines the core pipeline uses pre-collect); the RC hash/rep-selection kernel
   (`(A*u+B)%p` + sort_by.first) stays a compute kernel (native/pyarrow direct).
   REVIEWER: those engines are ALSO called from tui/engine.py:200,213 +
   core/chunked.py:101,106,295 (+ core/incremental.py:66-68, W5/core scope) — W4d must
   stamp the same `# W5:` boundary comments there; boundary comments satisfy the exit
   criterion, silence does not.
7. **Boundary-only files**: golden.py / identity.py / dataset.py are pl.from_arrow
   conversion adapters; sample.py / indicators.py are `from_dicts` constructors →
   `frame_from_records`.

## Batching (each its own PR, no stacking on unmerged predecessors)

- **W4a — seam ops, fixtures-first (ALL new ops for W4b-e live here)**:
  `filter_in(col, values)`; `group_collect_ids(key, id_col)` (stitching list-agg) IF
  reused else group_partitions; schema vocabulary `datetime_us` (bare pl.Datetime == us
  no-tz, matching resolve.py; arrow twin `pa.timestamp("us")`, mirroring the
  utf8→large_string mapping at frame.py:1160); `frame_from_records` (inference-parity
  fixtures: int/float/str/bool/None promotion, empty rows, nested→reject);
  `with_row_index_int64(name, offset)` (chunked offset arithmetic + web/preview
  int_range; existing with_row_index is uint32 `__row__`); `map_column(default=)`
  extension (strict-raise default preserved); `concat_frames(relaxed=True)`
  (vertical_relaxed); `with_group_len_over(key, name)` + `with_group_min_over(key,
  value, name)` (clustering windows); `with_pair_canonical(a, b)`
  (min_horizontal/max_horizontal); `unique_by(subset, keep)` (keep="last" ordering
  semantics pinned — clustering ~1237, pipeline.py:1970). Pre-decision (reviewer):
  resolve.py's `_stitch_new_record` row-aligned-to-live-dtypes construction stays a
  NATIVE branch (needs the full dtype lattice, out of _SCHEMA_DTYPES scope) — no op.
  W4e select+cast+alias shapes: reuse `select` + `with_column`/cast_str or
  frame_from_columns; if a byte-fit gap appears mid-batch, add `select_cast` then,
  fixtures-first. Polars impls byte-equal to the raw snippets; arrow twins parity-pinned.
- **W4b — identity**: resolve.py (filter_in + graph-bootstrap frames onto extended
  vocabulary + stitch row-aligned construction), stitching.py, fingerprint_batch arrow
  twin (golden-vector cross-pin). Gates: identity suites + fingerprint goldens unedited.
- **W4c — db + connectors**: connector contract stays DataFrame-shaped for polars lane;
  read/write boundaries via load_file/io_arrow where arrow-lane reachable; sync.py lazy
  plumbing kept, payload boundaries polymorphic; Null→Utf8 promotion recipe becomes a
  shared helper with an arrow twin. Gates: db suites, sync tests unedited.
- **W4d — chunked + web + tui + mcp/a2a handlers**: chunked derive/concat onto seam,
  scan-slice streaming stays native behind explicit backend branch (arrow lane: io_arrow
  chunked reader); web/tui filter/is_in/read sites; mcp/a2a read_csv sites → load_file;
  backend-leakage conversions (pl.exceptions, isinstance); `# W5:` boundary comments on
  tui/engine + chunked std/matchkey lazy-engine call sites. Gates: test_chunked.py,
  test_streaming.py, test_tui.py, web router suites, mcp/a2a suites.
- **W4e — distributed**: UDF-internal ports at the pl.from_arrow/.to_arrow bookends
  (identity_partition first — pure relational, best seam fit; then pipeline/golden
  adapters; then clustering label/WCC kernels; record_store bucketing incl the
  MANDATORY bucket_dir clear-on-materialize fix; scoring explode/canonicalize — its
  lazy std/matchkey chains stay native with a W5 boundary comment). RC checkpoint
  boundary + per-batch materialization PRESERVED (memory profile). GATES:
  bench-distributed-stack.yml 5M chain baseline BEFORE the batch, re-run after (wall +
  peak RSS within noise); unit suites: test_distributed_clustering(.py/_e2e),
  test_distributed_randomized_contraction_wcc, test_two_phase_wcc,
  test_distributed_scoring(+_tuning), test_distributed_pipeline(+_branch),
  test_distributed_golden, test_distributed_dataset/indicators/sample/block_shuffle,
  test_phase5_distributed_pipeline, test_ray_backend, test_prepared_record_store(+
  _controller/_pipeline), test_bucketed_store, test_partitioned_block_scoring_pipeline,
  test_score_buckets_*, test_sail_* parity stack, tests/identity/
  test_distributed_identity*. Ray tests in CI via .venv/bin/python NOT uv run.
- **W4f — downstream consumers**: SQL-extensions bridge JSON→Arrow, goldenmatch-duckdb
  `.arrow()`, goldenmatch-kg, dbt adapter surfaces (spec risk row). Survey at batch
  start; port or explicitly defer with notes.

## Exit gates (whole wave)

- Full goldenmatch shards + heavy + fallback + goldenmatch_frame_diff lanes green.
- bench-distributed-stack 5M chain: wall + peak RSS within noise of the pre-W4e baseline
  (run baseline BEFORE W4e lands, same runner class).
- Spec W4 exit ("Polars unreferenced" in tail areas) interpreted honestly: tail modules
  either (a) routed through the seam/io_arrow, or (b) carry an explicit
  `# W5:` native-polars boundary comment where the polars-default lane legitimately
  keeps them (LazyFrame plumbing, fingerprint polars twin) — zero SILENT usages.
- No default flip; GOLDENMATCH_FRAME stays polars. 2.x minor.

## Risks

| Risk | Mitigation |
| --- | --- |
| WCC memory profile destabilized (spec risk) | Baseline 5M chain bench pre-W4e; gate on wall+RSS |
| frame_from_records inference parity drift (polars vs pa.from_pylist) | Fixtures-first corpus: None columns, mixed int/float, bool, empty, nested reject |
| Datetime vocabulary extension leaks tz/unit ambiguity | Pin exactly one spelling (`datetime_us`, no tz) matching resolve.py's actual frames |
| sync.py weakref/staging GC hazard (#388) re-broken by refactor | Do NOT restructure the lazy plumbing; only touch payload boundaries; #388 test unedited |
| Ray batch_format flip changes shard boundaries/nondeterminism | (fill from recon) diff canonicalized outputs, not shard layout |
