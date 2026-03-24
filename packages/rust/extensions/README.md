# goldenmatch-extensions

Native SQL extensions for [GoldenMatch](https://github.com/benzsevern/goldenmatch) -- run entity resolution directly from PostgreSQL and DuckDB.

```sql
-- Deduplicate a table
SELECT goldenmatch_dedupe_table('customers', '{"exact": ["email"]}');

-- Match two tables
SELECT goldenmatch_match_tables('prospects', 'customers', '{"fuzzy": {"name": 0.85}}');

-- Score two strings
SELECT goldenmatch_score('John Smith', 'Jon Smyth', 'jaro_winkler');
-- 0.91

-- Score a pair of records
SELECT goldenmatch_score_pair(
    '{"name": "John Smith", "email": "j@x.com"}',
    '{"name": "Jon Smyth", "email": "j@x.com"}',
    '{"fuzzy": {"name": 0.85}, "exact": ["email"]}'
);
-- 0.95

-- Explain a match
SELECT goldenmatch_explain(
    '{"name": "John Smith", "email": "j@x.com"}',
    '{"name": "Jon Smyth", "email": "j@x.com"}',
    '{"fuzzy": {"name": 0.85}, "exact": ["email"]}'
);
```

## Architecture

```
goldenmatch-extensions/
├── bridge/     # Shared Rust crate: embeds Python via pyo3, calls goldenmatch
├── postgres/   # PostgreSQL extension via pgrx
└── duckdb/     # DuckDB extension (planned)
```

The extension embeds a CPython interpreter via [pyo3](https://pyo3.rs/) and calls the GoldenMatch Python package. Data flows through Apache Arrow for efficient interchange.

## Requirements

- PostgreSQL 15, 16, or 17
- Python 3.11+ with `pip install goldenmatch>=1.1.0`
- Rust toolchain (for building from source)

## Building from Source

```bash
# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Install pgrx
cargo install cargo-pgrx
cargo pgrx init --pg16=$(which pg_config)

# Build
cd goldenmatch-extensions
cargo pgrx package --pg-config=$(which pg_config)

# Install
cargo pgrx install --pg-config=$(which pg_config)
```

Then in PostgreSQL:
```sql
CREATE EXTENSION goldenmatch;
```

## Docker

```bash
docker build -t goldenmatch/postgres:16 .
docker run -p 5432:5432 -e POSTGRES_PASSWORD=postgres goldenmatch/postgres:16
# Extension is pre-installed. Connect and use:
# psql -h localhost -U postgres -c "SELECT goldenmatch.goldenmatch_score('John', 'Jon', 'jaro_winkler');"
```

## SQL Functions

### Table Operations (goldenmatch schema)

| Function | Description |
|----------|-------------|
| `goldenmatch_dedupe_table(table, config)` | Deduplicate a Postgres table |
| `goldenmatch_match_tables(target, ref, config)` | Match two Postgres tables |

### Scalar Functions (goldenmatch schema)

| Function | Description |
|----------|-------------|
| `goldenmatch_score(a, b, scorer)` | Score two strings (jaro_winkler, levenshtein, exact, etc.) |
| `goldenmatch_score_pair(rec_a, rec_b, config)` | Score two JSON records |
| `goldenmatch_explain(rec_a, rec_b, config)` | Explain a match in natural language |

### JSON Functions (goldenmatch schema)

| Function | Description |
|----------|-------------|
| `goldenmatch_dedupe(rows_json, config)` | Deduplicate JSON records directly |
| `goldenmatch_match(target_json, ref_json, config)` | Match two JSON record sets |

### Config Format

Config is a JSON object with optional keys:
```json
{
    "exact": ["email", "phone"],
    "fuzzy": {"name": 0.85, "address": 0.90},
    "blocking": ["zip"],
    "threshold": 0.85
}
```

## Roadmap

- **v0.1.0** -- PostgreSQL quick-start functions (current)
- **v0.2.0** -- Pipeline schema (`goldenmatch.configure()`, `goldenmatch.run()`, job management)
- **v0.3.0** -- DuckDB extension
- **v0.4.0** -- Distribution: Trunk, Docker, pre-built binaries

## License

MIT
