# goldenmatch-extensions

Native SQL extensions for [GoldenMatch](https://github.com/benzsevern/goldenmatch) -- run entity resolution directly from PostgreSQL and DuckDB.

```sql
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

-- Deduplicate records
SELECT goldenmatch_dedupe(
    '[{"name": "John", "email": "j@x.com"}, {"name": "JOHN", "email": "j@x.com"}]',
    '{"exact": ["email"]}'
);

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

## SQL Functions

### Quick-Start (public schema)

| Function | Description |
|----------|-------------|
| `goldenmatch_score(a, b, scorer)` | Score two strings (jaro_winkler, levenshtein, exact, etc.) |
| `goldenmatch_score_pair(rec_a, rec_b, config)` | Score two JSON records |
| `goldenmatch_dedupe(rows_json, config)` | Deduplicate JSON records |
| `goldenmatch_match(target, reference, config)` | Match two sets of JSON records |
| `goldenmatch_explain(rec_a, rec_b, config)` | Explain a match in natural language |

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
