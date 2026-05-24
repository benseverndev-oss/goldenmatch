# SQL Extensions

Run GoldenMatch entity resolution directly from SQL. Two backends -- PostgreSQL
(via [pgrx](https://github.com/pgcentralfoundation/pgrx)) and DuckDB (Python
UDFs) -- now expose the **same surface**, so a query written for one ports to
the other.

Both embed / call the `goldenmatch` Python package, so it must be installed into
the same Python the database uses.

## Install

### PostgreSQL

```sql
CREATE EXTENSION goldenmatch_pg;
SELECT goldenmatch.goldenmatch_score('John Smith', 'Jon Smyth', 'jaro_winkler');
```

All functions live in the `goldenmatch` schema -- qualify each call
(`goldenmatch.fn(...)`) or `SET search_path = goldenmatch, public;`. Pre-built
`.deb` / `.rpm` / `.tar.gz`, a Docker image, and a build-from-source recipe are
in the extensions repo.

### DuckDB

```python
import duckdb
import goldenmatch_duckdb

con = duckdb.connect()
goldenmatch_duckdb.register(con)
con.sql("SELECT goldenmatch_profile_table('customers')").show()
```

```bash
pip install goldenmatch-duckdb
pip install goldenflow   # optional, enables the goldenflow_* transforms
```

## Parity at a glance

On top of the original dedupe / match / score / identity functions, both
backends ship **13 core-API functions** and **8 GoldenFlow transforms**:

| Surface | PostgreSQL | DuckDB | Contract |
|---------|:---:|:---:|----------|
| Dedupe / match / score | yes | yes | table + JSON |
| Identity graph (read-only) | yes | yes | JSON |
| Core-API (13 functions) | yes | yes | JSON in / JSON out (identical) |
| GoldenFlow transforms (8) | yes | yes | scalar text -> text (byte-equivalent) |
| Pipeline / job management | yes | (use dedupe/match) | -- |

The core-API functions share an **identical JSON in / JSON out contract** across
backends; the GoldenFlow transforms are byte-equivalent. Pipeline job-management
functions (`gm_configure` / `gm_run` / ...) are Postgres-only -- DuckDB returns
JSON via `dedupe` / `dedupe_table` instead. PPRL and file-path functions are
deferred as not SQL-natural.

## Core-API functions

Thin wrappers over GoldenMatch's public core APIs. All return JSON text except
`goldenmatch_suggest_threshold`, which returns a floating-point value / SQL
`NULL` (unimodal or too-few-scores).

| Function | Wraps | Description |
|----------|-------|-------------|
| `goldenmatch_profile_table(table)` | `profile_dataframe` | Full profile report for a table |
| `goldenmatch_suggest_threshold(scores_json)` | `suggest_threshold` | Otsu threshold over a JSON score list |
| `goldenmatch_detect_domain(columns_json)` | `detect_domain` | Detect data domain from column names |
| `goldenmatch_extract_features(text, kind)` | `extract_*_features` | Structured features (`product`/`software`/`biblio`) |
| `goldenmatch_evaluate(pairs_json, ground_truth_json)` | `evaluate_pairs` / `evaluate_clusters` | Precision/recall/F1 vs. ground truth |
| `goldenmatch_compare_clusters(a_json, b_json)` | `compare_clusters` | CCMS / TWI comparison of two clusterings |
| `goldenmatch_validate_table(table, rules_json)` | `validate_dataframe` | Run validation rules; report + quarantine |
| `goldenmatch_autofix_table(table)` | `auto_fix_dataframe` | Common data fixes; fixes + fixed rows |
| `goldenmatch_detect_anomalies(table, sensitivity)` | `detect_anomalies` | Flag suspicious records (low/medium/high) |
| `goldenmatch_preflight(table, config_json)` | `preflight` | Pre-run config validation findings |
| `goldenmatch_postflight(table, config_json)` | `postflight` | Post-run signal report |
| `goldenmatch_train_em(rows_json, matchkey_json, params_json)` | `train_em` | Train Fellegi-Sunter m/u probabilities |
| `goldenmatch_score_probabilistic(rows_json, matchkey_json, em_result_json)` | `score_probabilistic` | Score pairs with a trained EMResult |

## GoldenFlow transforms

Scalar `text -> text` UDFs wrapping GoldenFlow's transform registry -- normalize
or canonicalize a column before matching. They fail open (pass the input through
unchanged) when `goldenflow` isn't installed.

| Function | GoldenFlow transform | Description |
|----------|----------------------|-------------|
| `goldenflow_normalize_email(value)` | `email_normalize` | Normalize an email address |
| `goldenflow_normalize_phone(value)` | `phone_e164` | Normalize a phone number to E.164 |
| `goldenflow_normalize_date(value)` | `date_iso8601` | Normalize a date to ISO-8601 |
| `goldenflow_normalize_name_proper(value)` | `name_proper` | Proper-case a personal name |
| `goldenflow_canonicalize_url(value)` | `url_normalize` | Canonicalize a URL |
| `goldenflow_canonicalize_address(value)` | `address_standardize` | Standardize a postal address |
| `goldenflow_strip(value)` | `strip` | Strip leading/trailing whitespace |
| `goldenflow_whitespace_normalize(value)` | `collapse_whitespace` | Collapse internal whitespace runs |

## Usage

### PostgreSQL

```sql
SET search_path = goldenmatch, public;

-- Profile a table (column stats, types, quality signals)
SELECT goldenmatch_profile_table('customers');

-- Otsu threshold suggestion (NULL when unimodal)
SELECT goldenmatch_suggest_threshold('[0.10, 0.12, 0.90, 0.92]');

-- Normalize columns inline before matching
SELECT
    goldenflow_normalize_email(email) AS email_norm,
    goldenflow_normalize_phone(phone) AS phone_e164
FROM customers;

-- Deduplicate a table
SELECT goldenmatch_dedupe_table('customers', '{"exact": ["email"]}');
```

### DuckDB

```python
con.sql("SELECT goldenmatch_profile_table('customers')").show()
con.sql("SELECT goldenmatch_suggest_threshold('[0.10, 0.12, 0.90, 0.92]')").show()
con.sql("""
    SELECT goldenflow_normalize_email(email) AS email_norm
    FROM customers
""").show()
con.sql("SELECT goldenmatch_dedupe_table('customers', '{\"exact\": [\"email\"]}')").show()
```

## Examples

Runnable snippets live in the monorepo at `examples/sql/duckdb_core_apis.sql`
and `examples/sql/postgres_core_apis.sql`. Per-backend function catalogs are in
the extension READMEs (`packages/rust/extensions/{postgres,duckdb}/README.md`).

## See also

- [Database Integration](Database-Integration) -- live Postgres sync via the
  CLI (`goldenmatch sync`), a different path from these in-database functions.
- [dbt Integration](dbt-Integration) -- run GoldenMatch from dbt over DuckDB.
