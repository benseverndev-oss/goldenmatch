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
release number, so one build loads on **any DuckDB >= 1.3.0** (a CI sweep LOADs
the single build on v1.3.0 / v1.3.2 / v1.4.0 / v1.5.4). The floor is 1.3.0, not
1.2.x, because DuckDB 1.2.x expects the older `linux_amd64_gcc4` platform string
while 1.3.0 unified it to `linux_amd64` -- a packaging detail, not the C API.
Five platforms are built, each proven by a real `LOAD` smoke in CI:
`linux_amd64`, `linux_arm64`, `osx_arm64`, `osx_amd64` (cross-built, smoked
under Rosetta), `windows_amd64`.

## Status: full transform catalogue

Essentially every `goldenflow-core` transform is now a SQL function -- **74 UDFs**:

| Group | Shape | Examples |
| ----- | ----- | -------- |
| single-arg `VARCHAR` | `fn(&str) -> String` | `email_normalize`, `name_proper`, `address_standardize`, all of text |
| single-arg nullable `VARCHAR` | `fn(&str) -> Option<String>` | `url_normalize`, `cc_format`, `iban_format` |
| `BOOLEAN` | `fn(&str) -> bool` / `Option<bool>` | `cc_validate`, `iban_validate`, `boolean_normalize`, `email_validate` |
| `DOUBLE` / `BIGINT` | `fn(&str) -> Option<f64/i64>` | `currency_strip`, `percentage_normalize`, `to_integer` |
| multi-output (component UDFs) | tuple -> one per part | `split_name_{first,last}`, `split_name_reverse_{first,last}`, `split_address_{street,city,state,zip}` |
| multi-arg | 2-3 args | `phone_{e164,national,country_code,valid}(phone, region)`, `truncate(s, n)`, `pad_{left,right}(s, width, pad)`, `merge_name(first, last)` |

`None` / null input / (for phone) non-NANP numbers map to SQL `NULL`. Multi-arg
UDFs follow SQL null-propagation (any NULL argument yields NULL without invoking
the kernel -- DuckDB scalar semantics, no override in duckdb-rs), so
`merge_name(NULL, 'Smith')` is NULL rather than the kernel's coalesced `'Smith'`;
non-NULL inputs stay byte-identical.

Phone is NANP-gated (`nanp_only`): the Rust port is byte-identical to Python
`phonenumbers` on country-code-1 numbers and returns `NULL` rather than a
possibly-wrong value elsewhere -- the same parity-safe posture as the native
kernel.

**Cross-surface proof:** the test suite threads the *entire* shared
`identifiers_corpus.jsonl` (489 rows, every single-arg transform) -- the exact
oracle the Python and TypeScript parity gates assert against -- through a real
in-process DuckDB; the multi-arg/output UDFs are checked against the reference
kernel directly. So the SQL surface is byte-identical to Python / TS / wasm by
the corpus, not just by construction.

Distribution builds + footers + LOAD-smokes the `.duckdb_extension` for five
platforms and publishes them on a `goldenflow-duckdb-v*` tag
(`.github/workflows/goldenflow-duckdb-dist.yml`).

**Not exposed:** `category_auto_correct` -- it builds a canonical map over an
entire column, so it is a DuckDB aggregate/table function, not a stateless
scalar; a separate surface. `date_*` transforms are excluded suite-wide (the
`dateutil` reference is fuzzy + non-deterministic, so not byte-portable).

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
