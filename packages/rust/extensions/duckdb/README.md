# goldenmatch-duckdb

GoldenMatch entity resolution functions for DuckDB.

```bash
pip install goldenmatch-duckdb
```

## Usage

```python
import duckdb
import goldenmatch_duckdb

con = duckdb.connect()
goldenmatch_duckdb.register(con)

# Score two strings
con.sql("SELECT goldenmatch_score('John Smith', 'Jon Smyth', 'jaro_winkler')").show()

# Deduplicate a table
con.sql("""
    CREATE TABLE customers AS SELECT * FROM (VALUES
        ('John', 'john@x.com'),
        ('JOHN', 'john@x.com'),
        ('Jane', 'jane@y.com')
    ) AS t(name, email)
""")
con.sql("SELECT goldenmatch_dedupe_table('customers', '{\"exact\": [\"email\"]}')").show()

# Match two tables
con.sql("SELECT goldenmatch_match_tables('prospects', 'reference', '{\"fuzzy\": {\"name\": 0.85}}')").show()
```

## Functions

| Function | Description |
|----------|-------------|
| `goldenmatch_score(a, b, scorer)` | Score two strings |
| `goldenmatch_score_pair(rec_a, rec_b, config)` | Score two JSON records |
| `goldenmatch_explain(rec_a, rec_b, config)` | Explain a match |
| `goldenmatch_dedupe_table(table, config)` | Deduplicate a DuckDB table |
| `goldenmatch_match_tables(target, ref, config)` | Match two DuckDB tables |
| `goldenmatch_dedupe(json, config)` | Deduplicate JSON records |
| `goldenmatch_match(target_json, ref_json, config)` | Match JSON records |

## Requirements

- Python 3.11+
- DuckDB 1.0+
- goldenmatch >= 1.1.0
