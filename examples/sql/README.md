# SQL usage examples

Run GoldenMatch entity resolution directly from SQL. Both backends expose the
same surface: **13 core-API functions** (`goldenmatch_*`) plus **8 GoldenFlow
transforms** (`goldenflow_*`), on top of the pre-existing dedupe / match /
score / identity functions.

| File | Backend | Demonstrates |
|---|---|---|
| `duckdb_core_apis.sql` | DuckDB | `goldenmatch_profile_table`, `goldenmatch_suggest_threshold`, `goldenflow_*` transforms, `goldenmatch_evaluate` |
| `postgres_core_apis.sql` | PostgreSQL | the same four, qualified to the `goldenmatch` schema |

## DuckDB

The UDFs are registered from Python, then you run SQL on the connection:

```python
import duckdb
import goldenmatch_duckdb

con = duckdb.connect()
goldenmatch_duckdb.register(con)
# now run any statement from duckdb_core_apis.sql, e.g.:
con.sql("SELECT goldenmatch_profile_table('customers')").show()
```

Install: `pip install goldenmatch-duckdb` (and `pip install goldenflow` to make
the `goldenflow_*` UDFs actually transform -- they fail open as pass-throughs
otherwise). See
[`packages/rust/extensions/duckdb/README.md`](../../packages/rust/extensions/duckdb/README.md).

## PostgreSQL

```sql
CREATE EXTENSION goldenmatch_pg;
\i postgres_core_apis.sql
```

All functions live in the `goldenmatch` schema. See
[`packages/rust/extensions/postgres/README.md`](../../packages/rust/extensions/postgres/README.md)
for build / install steps and the full function catalog.

## Notes

- `goldenmatch_*` core-API functions return JSON text. The one exception is
  `goldenmatch_suggest_threshold`, which returns `DOUBLE` / SQL `NULL`.
- `goldenflow_*` transforms are scalar `text -> text` and `STRICT` (NULL in ->
  NULL out). They pass the input through unchanged if `goldenflow` is not
  installed.
- The JSON in / JSON out contract is **identical across both backends**, so a
  call written for DuckDB ports to Postgres by qualifying the schema, and vice
  versa.
