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
  goldenflow_normalize_email(email)         AS email,
  goldenflow_normalize_name_proper(name)    AS name
FROM read_parquet('s3://bucket/raw/*.parquet');
```

## Status: Slice 1 (spike)

Two transforms are wired end-to-end to validate the mechanism:

| SQL function                       | Kernel                          |
| ---------------------------------- | ------------------------------- |
| `goldenflow_normalize_email`       | `goldenflow_core::email::email_normalize` |
| `goldenflow_normalize_name_proper` | `goldenflow_core::names::name_proper`     |

Next slices: table-drive the full byte-parity catalogue (`Slice 2`) and the
per-platform `.duckdb_extension` build + distribution (`Slice 3`).

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
