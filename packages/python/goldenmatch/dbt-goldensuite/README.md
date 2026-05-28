# dbt-goldensuite

dbt integration for [GoldenMatch](https://github.com/benseverndev-oss/goldenmatch) entity resolution.

## Installation

```bash
pip install dbt-goldensuite
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

## Macros

This package ships macros only (no models/seeds/snapshots). The
goldenmatch/goldencheck/goldenflow macros `adapter.dispatch()` between the
Postgres, DuckDB, and Snowflake extension function shapes; `infermap_apply`
is pure SQL (works on every adapter):

- **Dedupe materialization** -- `goldenmatch_dedupe` (`macros/materializations/`)
- **Identity graph** -- `identity_resolve`, `identity_list`, `identity_view`, `identity_history`, `identity_conflicts`
- **Learning memory** -- `file_field_correction`, `file_pair_correction`
- **Quality gates (GoldenCheck)** -- `quality_assert`, `quality_health_gate`, `quality_not_empty`
- **Transforms (GoldenFlow)** -- `transforms.sql`
- **Schema mapping (InferMap)** -- `infermap_apply(relation, column_map)` applies a
  Python-computed `infermap` column mapping as a plain projecting SELECT.

### Adapter coverage

| Adapter   | Status | Setup |
|-----------|--------|-------|
| Postgres  | Full -- 11 SQL functions + 5 pipeline functions via the `goldenmatch_pg` pgrx extension. | Install `goldenmatch_pg` per the extensions repo README. |
| DuckDB    | Full -- 7 UDFs + 5 identity-graph functions via `goldenmatch-duckdb`. | `pip install goldenmatch-duckdb` on the dbt-runner host. |
| Snowflake | Full -- same surface as DuckDB. Snowpark Python UDFs in the `goldenmatch` schema. `goldenmatch_dedupe` ships golden-only (matches the DuckDB v0.4.0 posture; clusters + pairs follow up). | See [docs/snowflake-setup.md](docs/snowflake-setup.md) for the wheel-upload + UDF registration steps. |
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
