# goldenflow-duckdb

A **zero-Python** DuckDB loadable extension that exposes GoldenFlow transforms
as SQL scalar functions, backed directly by the `goldenflow-core` reference
kernels. No CPython interpreter in the DuckDB process -- the same Rust kernels
that drive the Python, TypeScript, and WebAssembly surfaces run natively inside
the query engine, so results are byte-identical across all four surfaces by
construction.

```sql
LOAD 'goldenflow_duckdb.duckdb_extension';

SELECT
  goldenflow_email_normalize(email)      AS email,
  goldenflow_name_proper(name)           AS name,
  goldenflow_address_standardize(addr)   AS addr,
  goldenflow_url_normalize(website)      AS website  -- NULL when unparseable
FROM read_parquet('s3://bucket/raw/*.parquet');
```

UDF names are `goldenflow_<kernel>` -- a predictable 1:1 with the underlying
`goldenflow-core` function.

## Status: Slice 2 (VARCHAR catalogue)

The full single-argument `VARCHAR -> VARCHAR` catalogue is wired -- 36 UDFs
across address, email, names, text, and categorical families, in both the total
(`fn(&str) -> String`) and nullable (`fn(&str) -> Option<String>`, `None` ->
SQL `NULL`) shapes. Registration is table-driven, so adding a transform is one
`"name" => kernel` line.

Deferred to later slices:

| Slice | Scope |
| ----- | ----- |
| **2b** | Typed outputs: validators (`-> BOOLEAN`) + numeric parsers (`-> DOUBLE`/`BIGINT`), and the identifier family. Needs the primitive output-vector write API. |
| **3**  | Per-platform `.duckdb_extension` build (metadata footer + 5-target matrix) and distribution. |
| **later** | Multi-argument / multi-output kernels: phone (region arg), `split_*`, `truncate`, `pad_*`, `merge_name`, `auto_correct`. |

## Build & test

```sh
# Hermetic parity gate -- compiles DuckDB in-process and asserts SQL output
# equals the goldenflow-core reference kernel.
cargo test --no-default-features --features test-bundled

# The shippable artifact (links libduckdb at LOAD time via the C Extension API).
cargo build --release
```

Both run in CI (`.github/workflows/goldenflow-duckdb.yml`); the `bundled` build
is heavy, so CI is the authoritative build environment.
