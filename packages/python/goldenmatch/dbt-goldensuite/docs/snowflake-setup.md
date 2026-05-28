# Snowflake adapter setup

The Snowflake dispatch in `dbt-goldensuite` assumes a set of Snowpark
Python UDFs registered in the `goldenmatch` schema of your target
database. This file documents the one-time setup -- after that, the
dbt macros work the same as on Postgres / DuckDB.

## Approach

Snowflake doesn't have a pgrx-style native extension surface, and
`goldenmatch` isn't in Snowflake's curated Anaconda channel. The
working path is:

1. Build the `goldenmatch` wheel locally (or download from PyPI).
2. Upload the wheel + its runtime dependencies to a Snowflake stage.
3. Create Python UDFs that `IMPORTS` the staged wheel and call the
   `goldenmatch` Python API.

This is the same pattern `goldenmatch-duckdb` uses on DuckDB, with
Snowpark's `IMPORTS` clause replacing DuckDB's `create_function`
registration.

## Prerequisites

- Snowflake account on a tier that supports Python UDFs (any
  Standard or higher).
- A role with `CREATE FUNCTION` and `CREATE STAGE` on the target
  database.
- The `goldenmatch` wheel (`pip wheel goldenmatch` or
  download from PyPI).

## One-time setup

```sql
-- In the target database, e.g. ANALYTICS or your dbt target.database.
USE DATABASE <target_database>;
USE ROLE <role_with_create_function>;

-- 1. Schema that mirrors the Postgres convention.
CREATE SCHEMA IF NOT EXISTS goldenmatch;
USE SCHEMA goldenmatch;

-- 2. Stage to hold the wheel(s).
CREATE STAGE IF NOT EXISTS goldenmatch_wheels
    DIRECTORY = (ENABLE = TRUE);

-- 3. Upload the wheel from your laptop (snowsql or VS Code Snowflake
--    extension):
--    PUT file://./goldenmatch-1.x.x-py3-none-any.whl @goldenmatch_wheels;
--    PUT file://./polars-X.Y.Z-cp311-cp311-manylinux*.whl @goldenmatch_wheels;
--    PUT file://./rapidfuzz-X.Y.Z-cp311-cp311-manylinux*.whl @goldenmatch_wheels;
```

## Registering the UDFs

The simplest workflow ships a single dedupe UDF + the identity-graph
read functions. Adjust paths to match the wheels you uploaded.

```sql
-- Identity graph reads (5 functions). The Python handler defers to
-- goldenmatch.identity.store -- same code path as the DuckDB UDFs.

CREATE OR REPLACE FUNCTION goldenmatch.goldenmatch_identity_resolve(
    record_id STRING,
    db_path   STRING
)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
HANDLER = 'goldenmatch_udfs.identity_resolve'
IMPORTS = ('@goldenmatch.goldenmatch_wheels/goldenmatch-1.x.x-py3-none-any.whl')
PACKAGES = ('polars', 'pyarrow');

CREATE OR REPLACE FUNCTION goldenmatch.goldenmatch_identity_view(
    entity_id STRING,
    db_path   STRING
)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
HANDLER = 'goldenmatch_udfs.identity_view'
IMPORTS = ('@goldenmatch.goldenmatch_wheels/goldenmatch-1.x.x-py3-none-any.whl')
PACKAGES = ('polars', 'pyarrow');

-- ... identity_history, identity_conflicts, identity_list follow the
-- same shape. See scripts/snowflake_register.py for a script that
-- emits all of them.
```

```sql
-- Dedupe UDTF -- returns a table of golden records.

CREATE OR REPLACE FUNCTION goldenmatch.goldenmatch_dedupe_full(
    input_table STRING,
    config_json STRING
)
RETURNS TABLE(
    cluster_id BIGINT,
    -- columns mirror your input schema; you can also return VARIANT
    -- and unpack in dbt for a generic shape.
    golden VARIANT
)
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
HANDLER = 'goldenmatch_udfs.DedupeFull'
IMPORTS = ('@goldenmatch.goldenmatch_wheels/goldenmatch-1.x.x-py3-none-any.whl')
PACKAGES = ('polars', 'pyarrow', 'rapidfuzz');
```

```sql
-- Quality gates (GoldenCheck).
CREATE OR REPLACE FUNCTION goldenmatch.goldencheck_scan_table(
    relation_name STRING,
    domain        STRING
)
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
HANDLER = 'goldenmatch_udfs.scan_table'
IMPORTS = ('@goldenmatch.goldenmatch_wheels/goldenmatch-1.x.x-py3-none-any.whl')
PACKAGES = ('polars', 'pyarrow');

CREATE OR REPLACE FUNCTION goldenmatch.goldencheck_health_score(
    relation_name STRING
)
RETURNS FLOAT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
HANDLER = 'goldenmatch_udfs.health_score'
IMPORTS = ('@goldenmatch.goldenmatch_wheels/goldenmatch-1.x.x-py3-none-any.whl')
PACKAGES = ('polars', 'pyarrow');
```

```sql
-- Learning memory.
CREATE OR REPLACE FUNCTION goldenmatch.goldenmatch_correction_add(
    decision     STRING,
    dataset      STRING,
    memory_path  STRING,
    args_json    STRING
)
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
HANDLER = 'goldenmatch_udfs.correction_add'
IMPORTS = ('@goldenmatch.goldenmatch_wheels/goldenmatch-1.x.x-py3-none-any.whl')
PACKAGES = ('polars', 'pyarrow');
```

```sql
-- GoldenFlow transforms (8 functions, all scalar STRING -> STRING).
-- Example: normalize_email. The others follow the same shape.
CREATE OR REPLACE FUNCTION goldenmatch.goldenflow_normalize_email(s STRING)
RETURNS STRING
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
HANDLER = 'goldenmatch_udfs.normalize_email'
IMPORTS = ('@goldenmatch.goldenmatch_wheels/goldenmatch-1.x.x-py3-none-any.whl')
PACKAGES = ();
```

## Permissions

Reads (identity graph, dedupe, quality, transforms) are safe to grant
to all dbt roles:

```sql
GRANT USAGE ON SCHEMA goldenmatch
    TO ROLE <dbt_runner_role>;
GRANT USAGE ON FUNCTION goldenmatch.goldenmatch_identity_resolve(STRING, STRING)
    TO ROLE <dbt_runner_role>;
-- ...same pattern for each read UDF.
```

Writes (`goldenmatch_correction_add`) should be restricted to roles
that own steward review:

```sql
CREATE ROLE IF NOT EXISTS goldenmatch_correction_writer;
GRANT USAGE ON FUNCTION goldenmatch.goldenmatch_correction_add(
    STRING, STRING, STRING, STRING
) TO ROLE goldenmatch_correction_writer;
```

## Verifying the setup

Once the UDFs exist, the dbt macros render plain Snowflake SQL --
no extension manifest required. Smoke-test from the worksheet:

```sql
SELECT goldenmatch.goldencheck_health_score('my_table');

SELECT goldenmatch.goldenflow_normalize_email('  Foo@BAR.COM  ');
```

If both work, the dbt macros will work. Drop the `match_config` YAML
into your dbt repo and run a model with
`materialized='goldenmatch_dedupe'` against the Snowflake target.

## Caveats

- The `goldenmatch_dedupe_full` UDTF runs the full pipeline inside a
  single Snowflake worker. For large inputs (> 10M rows) prefer
  running the Python pipeline out-of-band with Snowpark Container
  Services or a dedicated runner; the warehouse-side UDTF is best
  for incremental / mid-sized batches.
- Snowpark Python UDFs read from the `IMPORTS` stage at compile time;
  rotating the wheel version means recreating the UDFs (or wrapping
  the version in a session-level alias).
- The native `goldenmatch[native]` Rust kernel is not yet available
  in Snowpark Python UDFs. The pure-Python fallback is used; for
  large workloads where native acceleration matters, prefer the
  out-of-band runner path.
