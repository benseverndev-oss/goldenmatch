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

## Status: Slice 2b (full single-arg catalogue)

Every single-argument transform in `goldenflow-core` is now a SQL function --
**58 UDFs** across four output shapes, table-driven (one `"name" => kernel` line
each):

| Output | Shape | Examples |
| ------ | ----- | -------- |
| `VARCHAR` | `fn(&str) -> String` | `email_normalize`, `name_proper`, `address_standardize`, all of text |
| `VARCHAR` (nullable) | `fn(&str) -> Option<String>` | `url_normalize`, `cc_format`, `iban_format`, `null_standardize` |
| `BOOLEAN` | `fn(&str) -> bool` / `Option<bool>` | `cc_validate`, `iban_validate`, `boolean_normalize`, `email_validate` |
| `DOUBLE` / `BIGINT` | `fn(&str) -> Option<f64/i64>` | `currency_strip`, `percentage_normalize`, `to_integer` |

`None` (and null input) map to SQL `NULL`.

**Cross-surface proof:** the test suite threads the *entire* shared
`identifiers_corpus.jsonl` (489 rows, every transform) -- the exact oracle the
Python and TypeScript parity gates assert against -- through a real in-process
DuckDB, so the SQL surface is byte-identical to Python / TS / wasm by the same
corpus, not just by construction.

Deferred to later slices:

| Slice | Scope |
| ----- | ----- |
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
