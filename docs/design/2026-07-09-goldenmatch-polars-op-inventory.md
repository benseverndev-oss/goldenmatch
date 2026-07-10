# goldenmatch Polars op inventory (W0 audit)

This is the W0 audit deliverable of the Polars-eviction program. Spec:
`docs/superpowers/specs/2026-07-09-goldenmatch-polars-eviction-design.md`.

Regenerate the census with:

```
python scripts/audit_goldenmatch_polars_ops.py
```

Census generated 2026-07-09 at commit `e2494d491` (branch
`feat/goldenmatch-polars-eviction-w0`). Scope: `packages/python/goldenmatch/goldenmatch/`
(all 154 files still touching `pl` after the W0 proxy sweep). Totals across the
whole package: **1,682** `pl.*` attribute uses, **1,401** curated relational
method calls (upper bound -- see caveat in the script docstring).

## Op families -> waves

Judgment calls below are hand-written against the actual census numbers (see
`## Generated census` for the raw tables). Counts combine the `pl.<fn>(...)`
attribute form and the `.method(...)` bound-call form where both exist for the
same op (e.g. `pl.read_csv` module fn vs `.read_csv()`/`.write_csv()` on a
frame).

### IO (`read_csv` / `scan_csv` / `read_parquet` / `write_*` / `read_excel`) -> W1 `io_arrow`
Counts: `pl.read_csv` 41, `pl.scan_csv` 6, `.read_csv(...)` 1, `pl.read_parquet`
5, `.read_parquet(...)` 2, `pl.read_excel` 3, `.write_csv(...)` 23,
`.write_parquet(...)` 14. `pl.scan_parquet` (9) sits outside the curated
method list (script gap -- `scan_parquet` wasn't added to `FRAME_METHODS`,
only `scan_csv` was) so its true call-site count is undercounted here; treat
`db/sync.py` and `core/chunked.py` (both IO-heavy in the table) as the
verification targets for that specific gap.
**Judgment: mechanical.** These are boundary reads/writes with well-defined
Arrow equivalents (`pyarrow.csv`, `pyarrow.parquet`); no domain semantics to
re-derive, just swap the call and re-verify byte-identical output on the
existing fixtures.

### Kernel boundary (`to_arrow`, `from_arrow`) -> already Arrow-shaped, W1 wiring
Counts: `.to_arrow(...)` 67, `.from_arrow(...)` 18, `pl.from_arrow` 34 (52
combined for the from-arrow direction).
**Judgment: mechanical.** These calls exist specifically because the native
kernels already speak Arrow -- the Polars frame is round-tripping through
`pyarrow.Table` on both sides of the FFI boundary today. Removing Polars here
means deleting the round-trip, not porting logic; lowest-risk, highest-value
piece of W1.

### Relational glue (`join`, `group_by`, `partition_by`, `unique`, `concat`) -> W2 seam ops
Counts: `.join(...)` 194, `.group_by(...)` 50, `.groupby(...)` 1 (deprecated
alias, one call site -- fold into the `group_by` port), `.partition_by(...)` 7,
`.unique(...)` 36, `.concat(...)` 5 + `pl.concat` 35 (40 combined).
**Judgment: needs-semantic-naming.** `join` is the single densest op
(194 calls) and spans multiple join semantics (inner candidate-pair joins in
`core/blocker.py`/`distributed/clustering.py`, left joins for enrichment in
`core/golden.py`/`identity/resolve.py`, asof-adjacent lookups elsewhere) --
the seam needs named variants (`Frame.join_inner`, `Frame.join_left`, etc.)
rather than one generic passthrough, or the eviction just re-imports Polars'
join-kwarg surface wholesale. `group_by`/`partition_by`/`concat` are more
uniform and closer to mechanical once `join` sets the pattern.

### Expression chains (`with_columns`, `select`, `filter`, `cast`, `concat_str`, `map_*`) -> W2/W3 semantic ops (named per call-site intent)
Counts: `.with_columns(...)` 121, `.select(...)` 130, `.filter(...)` 123
`cast` + 117 `filter`, `pl.concat_str` 6, `.map_elements(...)` 11,
`.map_batches(...)` 30.
**Judgment: needs-semantic-naming, and the hardest family.** These four verbs
(`with_columns`/`select`/`filter`/`cast`) are Polars' general-purpose
expression surface -- every call site embeds a different lambda/predicate, so
there is no single seam signature that covers them. The W2/W3 work is to name
each call site's *intent* (e.g. "add normalized-key column",
"drop null-key rows", "cast identity columns to Utf8") as its own `Frame`
method rather than exposing a raw expression builder. `map_elements` (Python
row-apply, already the slow path in Polars itself) and `map_batches` (chunked
numpy/arrow callback) are lower-volume but each needs a bespoke non-Polars
callback-invocation shape -- flag these individually per call site rather than
batching them into the generic expression-chain port.

### Column reductions (`n_unique`, `null_count`, `value_counts`) -> W3 (goldencheck-proven seam ops)
Counts: `.n_unique(...)` 39, `.null_count(...)` 14, `.value_counts(...)` 1.
**Judgment: mechanical.** goldencheck's column-profiler front (P0/A/A2/B/C,
see `project_goldencheck_polars_eviction` memory) already ported the
equivalent reductions to pyo3-free Arrow kernels; W3 here is porting the same
proven seam ops into goldenmatch rather than re-deriving them.

### Distributed/tails usage -> W4
Counts (all `distributed/*.py`, 11 files): 249 `pl.*` attrs, 237 relational
method calls, overwhelmingly concentrated in `distributed/clustering.py`
(172/167 -- by far the single densest file in the whole package).
`distributed/scoring.py` (23/27) and `distributed/pipeline.py` (22/23) are a
distant second/third. The remaining 8 distributed files are single digits to
low teens.
**Judgment: decline-shaped for `distributed/clustering.py` itself, mechanical
for the rest.** `clustering.py`'s size and density (WCC / cluster-shuffle
logic tied to Ray/GCS-backed distributed frames -- see
`project_844_distributed_wcc` and `project_datafusion_spine` memory) makes it
a poor W1-W3 candidate; it depends on distribution-aware frame semantics the
earlier waves won't have built yet. Defer the whole file to W4 rather than
partially porting it. The other 10 distributed files are thin enough to fold
into W2/W3 alongside their non-distributed counterparts once the pattern is
established, but are counted here under W4 for conservatism since they all
import from `distributed/` and share fixtures with `clustering.py`.

### Top-5 densest files and owning wave
| file | pl.* uses | relational calls | wave |
| --- | ---: | ---: | --- |
| `distributed/clustering.py` | 172 | 167 | W4 |
| `core/pipeline.py` | 98 | 132 | W2 (orchestrates the seam ops named above; ports once its callees port) |
| `core/survivorship/native.py` | 87 | 50 | W2/W3 (correlated survivorship -- field_groups/conditional field_rules, see `project_correlated_survivorship_plan`; expression-chain-heavy) |
| `core/blocker.py` | 50 | 63 | W1/W2 (blocking joins are the `join` seam's first proving ground -- candidate-pair inner joins, no distribution dependency) |
| `core/golden.py` | 58 | 49 | W2/W3 (survivorship expression chains + enrichment joins; `golden_fused.py` at 41/12 is the Rust-fused sibling already partially ported per `project_fused_golden_kernel`) |

Honorable mention: `core/scorer.py` (55/30) and `identity/fingerprint_batch.py`
(43/8) both carry the module-`__getattr__`-cached dtype constants noted below
and are otherwise expression-chain-dominated (W2/W3).

## Module-level import-time hazards (W0 findings)

- **2 dtype-set constants found by static recon**, both in
  `core/indicators.py`:
  - `_NON_IDENTITY_DTYPES = {pl.Boolean, pl.Date, pl.Datetime, pl.Time}` --
    fixed by deferring behind `@lru_cache(maxsize=1)` as
    `_non_identity_dtypes()` (commit `0e189ee51`).
  - `_BOOLEAN_DTYPES = {pl.Boolean}` -- deleted as dead code (no call sites
    referenced it).
- **2 more found only by the runtime import-gate test** (static grep missed
  both because they're multi-line / indirect literals, not a simple
  `{pl.X, ...}` on one line):
  - `core/scorer.py`: `PAIR_STREAM_SCHEMA` (a `dict[str, pl.DataType]`) --
    fixed via a cached builder (`_pair_stream_schema()`) plus a module-level
    `__getattr__` (PEP 562) so `from goldenmatch.core.scorer import
    PAIR_STREAM_SCHEMA` still resolves lazily for external consumers.
  - `identity/fingerprint_batch.py`: `_INT_UPCAST` (a tuple of dtypes) --
    fixed via a cached function (`_int_upcast_dtypes()`), same pattern as
    `indicators.py`.
- All 112 swept files already had `from __future__ import annotations`
  (no additional forward-ref fixes needed as part of the sweep).
- Zero `from polars import X` variants found anywhere in the package --
  every module-level import was the `import polars as pl` form, which
  simplified the proxy sweep to a single mechanical substitution
  (`from goldenmatch._polars_lazy import pl`).

**Lesson recorded:** static grep for `{pl\.` / `= {.*pl\.` patterns misses
multi-line literals and anything built via a helper call
(`_pair_stream_schema()`-style). The subprocess-based import-gate test (which
actually imports `goldenmatch` with polars absent from `sys.modules` and
asserts no `pl` symbol got touched at import time) is the authority on
whether a module is import-time-safe -- treat static recon as a first pass,
not a completeness guarantee.

## Generated census

<!-- BEGIN GENERATED: paste output of `python scripts/audit_goldenmatch_polars_ops.py` below verbatim -->

# goldenmatch Polars op census (generated)

| file | pl.* uses | relational method calls |
| --- | ---: | ---: |
| distributed/clustering.py | 172 | 167 |
| core/pipeline.py | 98 | 132 |
| core/survivorship/native.py | 87 | 50 |
| core/blocker.py | 50 | 63 |
| core/golden.py | 58 | 49 |
| core/autoconfig.py | 43 | 56 |
| core/scorer.py | 55 | 30 |
| db/sync.py | 45 | 40 |
| core/standardize.py | 44 | 18 |
| identity/resolve.py | 45 | 11 |
| core/golden_fused.py | 41 | 12 |
| core/chunked.py | 25 | 27 |
| identity/fingerprint_batch.py | 43 | 8 |
| distributed/scoring.py | 23 | 27 |
| core/matchkey.py | 33 | 15 |
| core/domain.py | 36 | 10 |
| core/autoconfig_controller.py | 36 | 9 |
| core/golden_rules_refiner.py | 15 | 30 |
| distributed/pipeline.py | 22 | 23 |
| core/cluster.py | 7 | 37 |
| core/indicators.py | 24 | 16 |
| tui/engine.py | 19 | 20 |
| core/autofix.py | 21 | 17 |
| core/block_analyzer.py | 21 | 16 |
| backends/score_buckets.py | 11 | 20 |
| _api.py | 10 | 19 |
| core/cluster_pairscores.py | 16 | 12 |
| documents/assemble.py | 20 | 5 |
| core/probabilistic.py | 13 | 11 |
| core/smart_ingest.py | 22 | 2 |
| core/suggest/adapter.py | 9 | 14 |
| core/schema_match.py | 17 | 5 |
| core/vector_store.py | 18 | 4 |
| core/agent.py | 12 | 9 |
| core/autoconfig_verify.py | 9 | 12 |
| core/validate.py | 12 | 9 |
| backends/datafusion_spine.py | 10 | 10 |
| core/fused_match.py | 8 | 12 |
| db/connector.py | 15 | 5 |
| core/ingest.py | 12 | 6 |
| core/memory/corrections.py | 10 | 8 |
| core/domain_registry.py | 10 | 7 |
| core/incremental.py | 7 | 9 |
| core/profiler.py | 10 | 6 |
| distributed/record_store.py | 10 | 6 |
| core/blocking_pass_selection.py | 6 | 9 |
| core/llm_extract.py | 8 | 7 |
| cli/label.py | 7 | 7 |
| core/quality.py | 12 | 2 |
| core/vector_index.py | 10 | 4 |
| core/api_connector.py | 13 | 0 |
| core/config_optimizer.py | 8 | 5 |
| core/learned_blocking.py | 3 | 10 |
| pprl/autoconfig.py | 6 | 7 |
| connectors/object_storage.py | 6 | 6 |
| core/dashboard.py | 5 | 7 |
| core/pairs.py | 0 | 12 |
| db/hybrid_blocking.py | 7 | 5 |
| identity/stitching.py | 5 | 7 |
| cli/rollback.py | 4 | 7 |
| connectors/sqlserver.py | 2 | 9 |
| distributed/golden.py | 10 | 1 |
| web/runs.py | 6 | 5 |
| a2a/skills.py | 8 | 2 |
| cli/anomalies.py | 5 | 5 |
| cli/pprl.py | 4 | 6 |
| core/transform.py | 4 | 6 |
| db/connector_mysql.py | 7 | 3 |
| db/connector_sqlserver.py | 7 | 3 |
| mcp/server.py | 3 | 7 |
| web/preview.py | 6 | 4 |
| connectors/mongo.py | 6 | 3 |
| connectors/postgres.py | 2 | 7 |
| core/diff.py | 5 | 4 |
| core/graph_er.py | 4 | 5 |
| core/llm_scorer.py | 2 | 7 |
| identity/store.py | 1 | 8 |
| core/boost.py | 4 | 4 |
| core/quality_exclusions.py | 4 | 4 |
| core/screening.py | 5 | 3 |
| core/streaming.py | 6 | 2 |
| db/connector_snowflake.py | 7 | 1 |
| pprl/protocol.py | 5 | 3 |
| connectors/duckdb_source.py | 2 | 5 |
| core/lineage.py | 3 | 4 |
| distributed/identity_partition.py | 0 | 7 |
| mcp/agent_tools.py | 5 | 2 |
| tui/tabs/boost_tab.py | 4 | 3 |
| connectors/mysql.py | 2 | 4 |
| connectors/redshift.py | 2 | 4 |
| core/blocking_candidates.py | 2 | 4 |
| core/lsh_blocker.py | 3 | 3 |
| core/perceptual_blocker.py | 3 | 3 |
| core/rag_surface.py | 4 | 2 |
| core/simhash_blocker.py | 3 | 3 |
| distributed/transforms.py | 4 | 2 |
| tui/tabs/matches_tab.py | 3 | 3 |
| web/routers/run.py | 4 | 2 |
| backends/duckdb_backend.py | 3 | 2 |
| backends/ray_backend.py | 1 | 4 |
| connectors/_sql_common.py | 4 | 1 |
| connectors/hubspot.py | 2 | 3 |
| core/frame.py | 1 | 4 |
| core/match_one.py | 3 | 2 |
| core/perceptual_autoconfig.py | 3 | 2 |
| core/preview.py | 3 | 2 |
| core/review_queue.py | 3 | 2 |
| db/writer.py | 3 | 2 |
| distributed/dataset.py | 1 | 4 |
| web/routers/match.py | 5 | 0 |
| api/server.py | 1 | 3 |
| backends/datafusion_backend.py | 0 | 4 |
| cli/autoconfig.py | 4 | 0 |
| cli/evaluate.py | 2 | 2 |
| cli/explain.py | 2 | 2 |
| cli/main.py | 1 | 3 |
| connectors/bigquery.py | 3 | 1 |
| connectors/salesforce.py | 2 | 2 |
| connectors/snowflake.py | 3 | 1 |
| core/autoconfig_discriminative.py | 3 | 1 |
| core/candidate_store.py | 1 | 3 |
| core/config_critique.py | 2 | 2 |
| core/llm_cluster.py | 1 | 3 |
| core/report.py | 2 | 2 |
| core/retrieval.py | 3 | 1 |
| identity/survivorship.py | 2 | 2 |
| snowflake/udfs.py | 2 | 2 |
| cli/dedupe.py | 1 | 2 |
| cli/incremental.py | 1 | 2 |
| connectors/databricks.py | 2 | 1 |
| core/evaluate.py | 1 | 2 |
| core/merge_preview.py | 2 | 1 |
| core/sensitivity.py | 1 | 2 |
| distributed/indicators.py | 3 | 0 |
| distributed/sample.py | 3 | 0 |
| output/writer.py | 1 | 2 |
| plugins/base.py | 3 | 0 |
| connectors/base.py | 2 | 0 |
| core/_hashing.py | 0 | 2 |
| core/anomaly.py | 1 | 1 |
| core/autoconfig_negative_evidence.py | 1 | 1 |
| core/graph.py | 1 | 1 |
| core/probabilistic_fast.py | 2 | 0 |
| cli/review.py | 1 | 0 |
| core/autoconfig_memory.py | 1 | 0 |
| core/survivorship/groups.py | 1 | 0 |
| core/survivorship/validate.py | 1 | 0 |
| core/tf_tables.py | 1 | 0 |
| distributed/identity.py | 1 | 0 |
| web/routers/autoconfig.py | 1 | 0 |
| web/routers/preview.py | 1 | 0 |
| web/routers/quality.py | 1 | 0 |
| web/routers/suggest.py | 1 | 0 |

## pl.* attribute totals

- `pl.DataFrame`: 600
- `pl.col`: 308
- `pl.Utf8`: 118
- `pl.Int64`: 117
- `pl.LazyFrame`: 75
- `pl.Series`: 60
- `pl.Expr`: 50
- `pl.lit`: 49
- `pl.read_csv`: 41
- `pl.concat`: 35
- `pl.from_arrow`: 34
- `pl.len`: 23
- `pl.Float64`: 23
- `pl.when`: 15
- `pl.Datetime`: 10
- `pl.scan_parquet`: 9
- `pl.String`: 9
- `pl.DataType`: 7
- `pl.scan_csv`: 6
- `pl.concat_str`: 6
- `pl.read_parquet`: 5
- `pl.Boolean`: 5
- `pl.element`: 5
- `pl.coalesce`: 5
- `pl.Int32`: 4
- `pl.Float32`: 4
- `pl.Null`: 4
- `pl.from_dicts`: 4
- `pl.read_excel`: 3
- `pl.repeat`: 3
- `pl.struct`: 3
- `pl.Date`: 3
- `pl.Time`: 3
- `pl.exceptions`: 3
- `pl.Categorical`: 2
- `pl.Duration`: 2
- `pl.Int8`: 2
- `pl.Int16`: 2
- `pl.UInt8`: 2
- `pl.UInt16`: 2
- `pl.UInt32`: 2
- `pl.max_horizontal`: 2
- `pl.Array`: 2
- `pl.List`: 2
- `pl.UInt64`: 2
- `pl.int_range`: 2
- `pl.from_pandas`: 1
- `pl.scan_ndjson`: 1
- `pl.read_json`: 1
- `pl.min_horizontal`: 1
- `pl.Binary`: 1
- `pl.Decimal`: 1
- `pl.Struct`: 1
- `pl.Object`: 1
- `pl.Enum`: 1

## Relational method totals (upper bound; hand-verify per wave)

- `.join(...)`: 194
- `.select(...)`: 130
- `.cast(...)`: 123
- `.with_columns(...)`: 121
- `.filter(...)`: 117
- `.collect(...)`: 114
- `.lazy(...)`: 97
- `.to_arrow(...)`: 67
- `.sort(...)`: 55
- `.group_by(...)`: 50
- `.n_unique(...)`: 39
- `.unique(...)`: 36
- `.is_in(...)`: 36
- `.agg(...)`: 35
- `.drop_nulls(...)`: 35
- `.map_batches(...)`: 30
- `.write_csv(...)`: 23
- `.from_arrow(...)`: 18
- `.null_count(...)`: 14
- `.write_parquet(...)`: 14
- `.map_elements(...)`: 11
- `.rename(...)`: 11
- `.partition_by(...)`: 7
- `.replace_strict(...)`: 6
- `.concat(...)`: 5
- `.over(...)`: 5
- `.hstack(...)`: 2
- `.read_parquet(...)`: 2
- `.value_counts(...)`: 1
- `.vstack(...)`: 1
- `.groupby(...)`: 1
- `.read_csv(...)`: 1
