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

## Install

Download `goldenflow_duckdb-<platform>.zip` from the
[`goldenflow-duckdb-v*` release assets](https://github.com/benseverndev-oss/goldenmatch/releases),
extract it, and `LOAD` the file. Extensions built outside the DuckDB signing
chain need the unsigned flag:

```sh
unzip goldenflow_duckdb-linux_amd64.zip   # -> goldenflow_duckdb.duckdb_extension
duckdb -unsigned
```
```sql
-- or, from any client
SET allow_unsigned_extensions = true;
LOAD '/path/to/goldenflow_duckdb.duckdb_extension';

SELECT goldenflow_email_normalize('  A.B@Example.COM ');  -- a.b@example.com
```

> The file **must** keep the name `goldenflow_duckdb.duckdb_extension` -- DuckDB
> derives the extension's init symbol from the filename, so a renamed file fails
> to load. (That's why the assets are per-platform zips of the correctly-named
> file rather than platform-suffixed bare extensions.)

**Portable across DuckDB versions:** the extension targets the *stable* C API
(`v1.2.0`), which is versioned separately from -- and well below -- the DuckDB
release number, so one build loads on **any DuckDB >= 1.2.0** (smoke-tested
against the current v1.5.4 CLI). Five platforms are built and each proven by a
real `LOAD` smoke in CI: `linux_amd64`, `linux_arm64`, `osx_arm64`, `osx_amd64`
(cross-built, smoked under Rosetta), `windows_amd64`.

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

Distribution (Slice 3) builds + footers + LOAD-smokes the `.duckdb_extension`
for three platforms and publishes them on a `goldenflow-duckdb-v*` tag
(`.github/workflows/goldenflow-duckdb-dist.yml`).

Deferred to later slices:

| Slice | Scope |
| ----- | ----- |
| **later** | Multi-DuckDB-version builds; multi-argument / multi-output kernels: phone (region arg), `split_*`, `truncate`, `pad_*`, `merge_name`, `auto_correct`. |

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
