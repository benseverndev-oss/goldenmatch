# dbt-goldensuite

dbt integration for [GoldenMatch](https://github.com/benseverndev-oss/goldenmatch) entity resolution.

## Installation

`dbt-goldensuite` ships inside the [Golden Suite monorepo](https://github.com/benseverndev-oss/goldenmatch) and is consumed from there (it is not published to PyPI).

**As a dbt package** (the macros + materialization) — add to your `packages.yml`:

```yaml
packages:
  - git: "https://github.com/benseverndev-oss/goldenmatch.git"
    subdirectory: "packages/dbt/goldensuite"
```

then run `dbt deps`.

**The Python helper** (`run_goldenmatch_dedupe`, correction CRUD) — install the subdirectory directly:

```bash
pip install "git+https://github.com/benseverndev-oss/goldenmatch.git#subdirectory=packages/dbt/goldensuite"
```

## Usage

Run GoldenMatch deduplication on a DuckDB table:

```python
from dbt_goldensuite.materialize import run_goldenmatch_dedupe

result = run_goldenmatch_dedupe(
    input_table="raw_customers",
    config_path="match.yaml",
    output_table="deduped_customers",
    database="warehouse.duckdb",
)
print(f"Deduped {result['input_rows']} -> {result['clusters']} clusters")
```

### Probabilistic (Fellegi-Sunter) entity resolution -- zero config

```sql
{{ config(materialized='goldenmatch_dedupe', probabilistic=true) }}
SELECT * FROM {{ ref('stg_customers') }}
```

`probabilistic=true` builds a Fellegi-Sunter model from your data with no
hand-written config (omit `match_config`). Setting both `match_config` and
`probabilistic=true` is a compile-time error. Inspect the derived model
directly (the m/u weights + comparison levels):

```sql
SELECT goldenmatch_autoconfig('stg_customers', 'probabilistic');
```

Omitting both `match_config` and `probabilistic` runs standard zero-config
dedupe. Fellegi-Sunter needs enough rows with agreeing fields to train EM; a
too-small table fails the model with a clear error rather than producing empty
output. DuckDB + Postgres only in this release (Snowflake + `probabilistic`
raises a clear error; use an explicit `match_config` there). The Python helper
takes the same flag: `run_goldenmatch_dedupe(..., probabilistic=True)` (no
`config_path`).

### Match-quality test

Gate a dbt build on entity-resolution quality. `goldenmatch_match_quality` is a
pure-SQL generic test (portable across adapters, no UDF) that compares a dedupe
model's predicted pairs against a ground-truth pairs table and fails the build
when precision/recall/F1 drops below the configured floors:

```yaml
models:
  - name: deduped_customers
    tests:
      - goldenmatch_match_quality:
          ground_truth: ref('customer_truth')   # table of true matching pairs
          input: clusters                        # 'clusters' (default) | 'pairs'
          min_f1: 0.90
          min_precision: 0.80                    # set any subset; at least one required
```

- `input: clusters` expands within-cluster pairs (model columns `record_id`,
  `cluster_id`); `input: pairs` uses the model's `id_a`/`id_b` pairs. Column
  names are overridable (`record_id`/`cluster_id`/`pairs_a`/`pairs_b`/`gt_a`/`gt_b`).
- Metrics are pairwise (clusters expanded to pairs, canonicalized `(min,max)`).
  The test fails (returns the metrics row) when any provided floor is violated;
  an empty/garbage model fails rather than silently passing.

### Two-table match (record linkage)

Link a target model against a reference table (the cross-table ER use case: match
an incoming list against a master). `goldenmatch_match` is a materialization:

```sql
{{ config(materialized='goldenmatch_match', reference=ref('master_customers')) }}
SELECT * FROM {{ ref('incoming_leads') }}
```

The model body is the **target**; `reference` is the master relation. Output is a
matched-pairs table `(target_id, reference_id, score)` — best match per target.
`target_id`/`reference_id` are 0-based row indices into the staged target / reference;
join back via `ROW_NUMBER() OVER (ORDER BY ...) - 1` on the same inputs.

`match_config` is optional: omit it for zero-config auto-matching (the reliable
default); pass an explicit config (`match_df`'s `exact`/`fuzzy`/`blocking`/`threshold`
shape — include blocking so candidates are generated) for control. **Postgres-first**
(DuckDB raises a clear error; use the `goldenmatch_match_tables` JSON UDF on DuckDB).

## Macros

This package ships macros only (no models/seeds/snapshots). The
goldenmatch/goldencheck/goldenflow macros `adapter.dispatch()` between the
Postgres, DuckDB, and Snowflake extension function shapes; `infermap_apply`
is pure SQL (works on every adapter):

- **Dedupe materialization** -- `goldenmatch_dedupe` (`macros/materializations/`)
- **Match materialization** -- `goldenmatch_match` two-table record linkage (target model + `reference` ref -> matched pairs). Postgres-first. See [above](#two-table-match-record-linkage).
- **Identity graph** -- `identity_resolve`, `identity_list`, `identity_view`, `identity_history`, `identity_conflicts`
- **Learning memory** -- `file_field_correction`, `file_pair_correction`
- **Quality gates (GoldenCheck)** -- `quality_assert`, `quality_health_gate`, `quality_not_empty`
- **Match-quality test** -- `goldenmatch_match_quality` generic test: gate a dbt build on pairwise precision/recall/F1 vs ground truth. Pure SQL (portable, no UDF). See [below](#match-quality-test).
- **Transforms (GoldenFlow)** -- `transforms.sql`
- **Schema mapping (InferMap)** -- `infermap_apply(relation, column_map)` applies a
  Python-computed `infermap` column mapping as a plain projecting SELECT.
- **Snowflake Cortex** -- `cortex_embed_768`, `cortex_embed_1024`, `cortex_embed` (dim-dispatched), `cortex_cosine_similarity`, `cortex_l2_distance`, `cortex_inner_product`, `cortex_complete`. In-warehouse embeddings + LLM. Snowflake-only. Pairs with the `snowflake_cortex` provider in `goldenmatch.embeddings` for parity between dbt models and Python code. See [docs/snowflake-cortex.md](docs/snowflake-cortex.md).

### Adapter coverage

| Adapter   | Status | Setup |
|-----------|--------|-------|
| Postgres  | Full -- 11 SQL functions + 5 pipeline functions via the `goldenmatch_pg` pgrx extension. | Install `goldenmatch_pg` per the extensions repo README. |
| DuckDB    | Full -- 7 UDFs + 5 identity-graph functions via `goldenmatch-duckdb`. | `pip install goldenmatch-duckdb` on the dbt-runner host. |
| Snowflake (Snowpark Python UDFs) | Full -- same SQL surface as the Postgres path. `goldenmatch_dedupe` supports all three output shapes (`golden`, `clusters`, `pairs`). Pure-Python (Anaconda's Snowflake channel doesn't carry `goldenmatch-native`). | See [docs/snowflake-setup.md](docs/snowflake-setup.md) for the wheel-upload + UDF registration steps. |
| Snowflake (SPCS service functions) | Same SQL surface; same dbt macros; backed by `goldenmatch[native]` in a Snowpark Container Services container. Use for workloads where native acceleration matters. | See [docs/snowflake-spcs.md](docs/snowflake-spcs.md) for the Dockerfile + service spec + setup walkthrough. |
| Others    | Compile error with a remediation hint pointing at `goldenmatch.dedupe_df()` / `goldenflow.transform_df()` / `goldencheck` Python helpers. | n/a |

### GoldenPipe orchestration in dbt

GoldenPipe's value is *adaptive* orchestration (profile the data, then decide
whether to clean / dedupe) — that decisioning is Python-side and not
SQL-expressible, so there is no `goldenpipe` dispatch macro. The explicit
pipeline composes the macros above: apply `transforms.sql` in one model, then
materialize the result with `goldenmatch_dedupe`. Run the adaptive pipeline via
the `goldenpipe` Python package / CLI / MCP when you need the decisioning.

## Status

Early stage -- API may change. Covers GoldenMatch (dedupe + identity), GoldenCheck
(quality gates), GoldenFlow (transforms), and InferMap (`infermap_apply`).
GoldenPipe's adaptive orchestration stays Python-side (compose the macros above
for the explicit pipeline).
