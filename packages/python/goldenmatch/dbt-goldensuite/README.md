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

This package ships macros only (no models/seeds/snapshots). They `adapter.dispatch()`
between the Postgres and DuckDB extension function shapes:

- **Dedupe materialization** -- `goldenmatch_dedupe` (`macros/materializations/`)
- **Identity graph** -- `identity_resolve`, `identity_list`, `identity_view`, `identity_history`, `identity_conflicts`
- **Learning memory** -- `file_field_correction`, `file_pair_correction`
- **Quality gates (GoldenCheck)** -- `quality_assert`, `quality_health_gate`, `quality_not_empty`
- **Transforms (GoldenFlow)** -- `transforms.sql`

## Status

Early stage -- API may change. Covers GoldenMatch (dedupe + identity), GoldenCheck
(quality gates), and GoldenFlow (transforms); goldenpipe orchestration and infermap
mapping are not yet exposed as dbt macros.
