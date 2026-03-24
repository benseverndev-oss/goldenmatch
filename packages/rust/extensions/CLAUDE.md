# GoldenMatch Extensions

Native SQL extensions for [GoldenMatch](https://github.com/benzsevern/goldenmatch) (`D:\show_case\goldenmatch`).

## Related Projects
- **Main repo:** `D:\show_case\goldenmatch` -- Python entity resolution toolkit (v1.1.0+). Has its own CLAUDE.md with full architecture docs.
- **This repo:** Rust bridge + Postgres extension + DuckDB Python UDFs
- **PyPI packages:** `goldenmatch` (Python), `goldenmatch-duckdb` (DuckDB UDFs)

## Environment
- Windows 11, bash shell (Git Bash) -- use Unix paths
- Two GitHub accounts: `benzsevern` (personal, for this repo) and `benzsevern-mjh` (work)
- MUST `gh auth switch --user benzsevern` before push, switch back to `benzsevern-mjh` after
- Rust 1.94.0 at `C:\Users\bsevern\.cargo\bin` -- must set `RUSTUP_HOME="C:/Users/bsevern/.rustup"` and `CARGO_HOME="C:/Users/bsevern/.cargo"` in every bash command
- No admin privileges -- no LLVM/libclang, no WSL2. pgrx builds only work in CI (Linux)
- PostgreSQL 16 portable at `C:\Users\bsevern\tools\pg16portable\pgsql`
- Bridge crate compiles locally. Postgres crate requires Linux (CI only).

## Architecture
- Rust workspace (`Cargo.toml`) contains only `bridge/` crate
- `postgres/` is excluded from workspace (`exclude = ["postgres"]`) -- pgrx 0.12.9 bug with SQL generation in workspace mode
- `duckdb/` is a standalone Python package (not Rust)

### bridge/ (goldenmatch-bridge)
- Shared crate: embeds CPython via pyo3, calls goldenmatch Python API
- `api.rs` -- wrappers for dedupe, match, score_strings, score_pair, explain_pair, dedupe_pairs (structured), dedupe_clusters (structured)
- `convert.rs` -- JSON <-> Polars DataFrame conversion (future: Arrow C Data Interface)
- `error.rs` -- BridgeError enum with From impls for PyErr and ArrowError

### postgres/ (goldenmatch_pg)
- pgrx 0.12.9 Postgres extension, standalone crate (not in workspace)
- `quick.rs` -- 9 SQL functions: table-based (SPI), table-returning (TableIterator), scalar, JSON-based
- `pipeline.rs` -- 5 job management functions: gm_configure, gm_run, gm_jobs, gm_golden, gm_drop
- `spi.rs` -- reads PG tables via `row_to_json()` SPI queries
- SQL file at `sql/goldenmatch_pg--0.1.0.sql` -- handwritten (pgrx doesn't auto-generate)
- .control file: `schema = goldenmatch` -- all functions in goldenmatch schema

### duckdb/ (goldenmatch-duckdb)
- Python package: `pip install goldenmatch-duckdb`
- `functions.py` -- registers 7 DuckDB UDFs via `con.create_function()`
- Table-reading UDFs use `con.cursor()` to avoid deadlock (UDF can't query same connection)
- Requires `pyarrow` for DuckDB `.pl()` Polars conversion

## Testing
- `cargo build -p goldenmatch-bridge` -- builds bridge locally (works on Windows)
- `cargo test -p goldenmatch-bridge` -- runs bridge tests (needs goldenmatch Python package installed)
- Postgres extension: build/test only via CI (needs libclang + PG dev headers)
- DuckDB: `cd duckdb && pip install -e . && python -m pytest tests/ -v`

## CI
- 4 jobs: lint, bridge-tests, postgres-build, duckdb-tests
- Lint: `cargo fmt --check` (bridge + postgres separately) + `cargo clippy` (bridge only)
- Postgres CI: installs PG16 + libclang, builds with pgrx, tests via psql (not pgrx test)
- Release workflow (`.github/workflows/release.yml`): triggered by GitHub Release, builds .tar.gz binary + Docker image to ghcr.io

## Gotchas
- pgrx 0.12.9 does NOT auto-generate SQL files -- must maintain `sql/goldenmatch_pg--0.1.0.sql` manually
- pgrx extension functions are in `goldenmatch` schema -- use `goldenmatch.function_name()` in psql, or explicit `::TEXT` casts
- `cargo` defaults CARGO_HOME to drive root on Windows when CWD is D: -- always set explicitly
- DuckDB UDFs cannot query same connection (deadlock) -- use `con.cursor()` for table reads
- `cargo fmt` must run separately for bridge (workspace) and postgres (standalone)
- PyPI publishing uses credentials from `D:\show_case\goldenmatch\.testing\.env`
