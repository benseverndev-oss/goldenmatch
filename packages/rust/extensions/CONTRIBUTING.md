# Contributing to GoldenMatch Extensions

Thanks for your interest in contributing! This repo has two components: a Postgres extension (Rust/pgrx) and DuckDB UDFs (Python).

## Quick Start -- DuckDB UDFs (Python)

```bash
git clone https://github.com/benzsevern/goldenmatch-extensions.git
cd goldenmatch-extensions/duckdb
pip install -e ".[dev]"
pytest --tb=short
```

## Quick Start -- Postgres Extension (Rust)

The Postgres extension uses pgrx and requires Linux for builds. You will need:

- Rust toolchain (stable)
- LLVM/libclang (required by pgrx for C bindings)
- PostgreSQL dev headers

Local Rust builds on Windows are not supported (no LLVM). Use CI or a Linux environment.

```bash
cd postgres
cargo test        # Linux/CI only
cargo pgrx run    # Linux/CI only
```

## Ways to Contribute

### Python DuckDB UDFs

- Add or improve UDF functions in the `duckdb/` directory
- Follow existing patterns for function registration
- All Python changes can be developed and tested on any platform

### Postgres Extension

- Rust code lives in `postgres/`
- SQL definitions in `postgres/sql/`
- Test locally on Linux or rely on CI for validation
- pgrx builds need LLVM -- CI handles this automatically

### Fix Bugs or Add Features

1. Fork the repo
2. Create a branch (`git checkout -b feat/my-feature`)
3. Make your changes
4. Run the appropriate tests (see below)
5. Submit a PR

## Testing

| Component | Command | Platform |
|-----------|---------|----------|
| DuckDB UDFs | `pytest --tb=short` (from `duckdb/`) | Any |
| Postgres ext | `cargo test` | Linux/CI only |

CI runs four jobs: lint, bridge tests, Postgres extension, and DuckDB tests.

## Development Guidelines

### Code Style

- Python: `ruff` for linting, type hints encouraged
- Rust: `cargo fmt` and `cargo clippy`

### Commit Messages

Use conventional commits:

```
feat: add similarity UDF for DuckDB
fix: handle NULL input in pg extension
docs: update build instructions
```

### Pull Requests

- Squash merge all PRs (clean history on main)
- PR title follows conventional commit format: `feat: ...` or `fix: ...`
- Include a summary and test plan in the PR body

## Questions?

Open an issue on the repo.
