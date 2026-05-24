# goldenmatch_pg

GoldenMatch entity resolution functions for PostgreSQL, built with
[pgrx](https://github.com/pgcentralfoundation/pgrx). The extension embeds a
CPython interpreter and calls the [`goldenmatch`](https://pypi.org/project/goldenmatch/)
Python package, so install that into the same Python the server uses.

## Install

```sql
CREATE EXTENSION goldenmatch_pg;
-- Verify:
SELECT goldenmatch.goldenmatch_score('John Smith', 'Jon Smyth', 'jaro_winkler');
```

All functions live in the **`goldenmatch` schema**. Either qualify each call
(`goldenmatch.fn(...)`) or `SET search_path = goldenmatch, public;` first.

See the [extensions README](../README.md) for pre-built binaries (`.deb` /
`.rpm` / `.tar.gz`), the Docker image, and the build-from-source recipe
(`cargo pgrx install`, Python 3.11+, PG dev headers, libclang).

## Usage

```sql
SET search_path = goldenmatch, public;

-- Score two strings
SELECT goldenmatch_score('John Smith', 'Jon Smyth', 'jaro_winkler');

-- Deduplicate a table
CREATE TABLE customers (name TEXT, email TEXT);
INSERT INTO customers VALUES ('John', 'john@x.com'), ('JOHN', 'john@x.com'), ('Jane', 'jane@y.com');
SELECT goldenmatch_dedupe_table('customers', '{"exact": ["email"]}');

-- Match two tables
SELECT goldenmatch_match_tables('prospects', 'customers', '{"fuzzy": {"name": 0.85}}');
```

## Functions

### Table operations

| Function | Description |
|----------|-------------|
| `goldenmatch_dedupe_table(table, config)` | Deduplicate a Postgres table |
| `goldenmatch_match_tables(target, ref, config)` | Match two Postgres tables |
| `goldenmatch_dedupe_pairs(table, config)` | Dedupe -> table of `(id_a, id_b, score)` |
| `goldenmatch_dedupe_clusters(table, config)` | Dedupe -> table of `(cluster_id, record_id, cluster_size)` |

### Scalar functions

| Function | Description |
|----------|-------------|
| `goldenmatch_score(a, b, scorer)` | Score two strings (jaro_winkler, levenshtein, exact, ...) |
| `goldenmatch_score_pair(rec_a, rec_b, config)` | Score two JSON records |
| `goldenmatch_explain(rec_a, rec_b, config)` | Explain a match in natural language |

### JSON functions

| Function | Description |
|----------|-------------|
| `goldenmatch_dedupe(rows_json, config)` | Deduplicate JSON records directly |
| `goldenmatch_match(target_json, ref_json, config)` | Match two JSON record sets |

### Pipeline / job management

| Function | Description |
|----------|-------------|
| `gm_configure(job, config)` | Store a named job config |
| `gm_run(job, table)` | Run a stored job against a table |
| `gm_jobs()` | List configured jobs |
| `gm_golden(job)` | Golden records for a job |
| `gm_drop(job)` | Drop a job + its results |
| `gm_pairs(job)` | Table of `(id_a, id_b, score)` for a job |
| `gm_clusters(job)` | Table of `(cluster_id, record_id)` for a job |
| `gm_telemetry(job)` | Last run's AutoConfigController telemetry |

### AutoConfig

| Function | Description |
|----------|-------------|
| `goldenmatch_autoconfig(table)` | Run the controller, return the committed `GoldenMatchConfig` JSON |
| `goldenmatch_autoconfig_telemetry(table)` | Same, but return the controller telemetry blob |
| `goldenmatch_dedupe_full(table, config)` | Dedupe with a full `GoldenMatchConfig` (negative evidence, per-matchkey scorers, ...) |
| `goldenmatch_dedupe_full_telemetry(table, config)` | Same, returning that run's telemetry |

### Identity graph (read-only)

| Function | Description |
|----------|-------------|
| `goldenmatch_identity_resolve(record_id, db_path)` | Resolve `{source}:{pk}` to its identity view |
| `goldenmatch_identity_view(entity_id, db_path)` | Full identity view JSON |
| `goldenmatch_identity_history(entity_id, db_path)` | Temporal event log (JSON array) |
| `goldenmatch_identity_conflicts(dataset, db_path)` | `conflicts_with` evidence edges (JSON array) |
| `goldenmatch_identity_list(dataset, status, db_path)` | List identities (empty filter = all) |

### Corrections (Learning Memory)

| Function | Description |
|----------|-------------|
| `correction_add(...)` | File a pair / field / cluster correction (REVOKEd from PUBLIC; grant `goldenmatch_correction_writer`) |
| `correction_list(dataset, memory_path)` | List corrections (JSON) |
| `goldenmatch.corrections` (view) | `SELECT * FROM goldenmatch.corrections WHERE dataset = '...'` |

### Core-API functions

Thin wrappers over GoldenMatch's public core APIs -- **identical JSON in / JSON
out contract to the DuckDB `goldenmatch_*` core-API UDFs**, so the two backends
are interchangeable. Table-input functions read the named table via SPI; all
read-only. Every function returns JSON text except `goldenmatch_suggest_threshold`,
which returns `DOUBLE PRECISION` / SQL `NULL`.

| Function | Wraps | Description |
|----------|-------|-------------|
| `goldenmatch_profile_table(table)` | `profile_dataframe` | Full profile report for a table (JSON) |
| `goldenmatch_suggest_threshold(scores_json)` | `suggest_threshold` | Otsu threshold over a JSON score list (NULL when unimodal) |
| `goldenmatch_detect_domain(columns_json)` | `detect_domain` | Detect data domain from a JSON column-name list |
| `goldenmatch_extract_features(text, kind)` | `extract_*_features` | Extract structured features; `kind` = `product`/`electronics`, `software`, or `biblio` |
| `goldenmatch_evaluate(pairs_json, ground_truth_json)` | `evaluate_pairs` / `evaluate_clusters` | Precision/recall/F1 vs. ground truth (auto-selects by shape) |
| `goldenmatch_compare_clusters(a_json, b_json)` | `compare_clusters` | CCMS / TWI comparison of two clusterings |
| `goldenmatch_validate_table(table, rules_json)` | `validate_dataframe` | Apply validation rules; returns report + quarantined rows |
| `goldenmatch_autofix_table(table)` | `auto_fix_dataframe` | Apply common data fixes; returns fixes + fixed rows |
| `goldenmatch_detect_anomalies(table, sensitivity)` | `detect_anomalies` | Flag suspicious records (`low`/`medium`/`high`) |
| `goldenmatch_preflight(table, config_json)` | `preflight` | Pre-run config validation findings |
| `goldenmatch_postflight(table, config_json)` | `postflight` | Post-run signal report (runs dedupe to derive pair scores) |
| `goldenmatch_train_em(rows_json, matchkey_json, params_json)` | `train_em` | Train Fellegi-Sunter m/u probabilities; returns EMResult JSON |
| `goldenmatch_score_probabilistic(rows_json, matchkey_json, em_result_json)` | `score_probabilistic` | Score pairs with a trained EMResult |

```sql
-- Otsu threshold suggestion
SELECT goldenmatch_suggest_threshold('[0.1, 0.12, 0.9, 0.92]');

-- Profile a table
SELECT goldenmatch_profile_table('customers');

-- Detect domain from columns
SELECT goldenmatch_detect_domain('["product_title", "brand", "sku"]');
```

### GoldenFlow transforms

8 scalar `text -> text` functions wrapping GoldenFlow's transform registry --
normalize / canonicalize a column before matching. Byte-equivalent to the
DuckDB `goldenflow_*` UDFs. All `STRICT` (NULL in -> NULL out) and fail open
(pass the input through unchanged) when `goldenflow` isn't installed, so they
are read-only and safe for PUBLIC.

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

```sql
-- Normalize columns inline before matching
SELECT
    goldenflow_normalize_email(email)     AS email_norm,
    goldenflow_normalize_phone(phone)     AS phone_e164,
    goldenflow_whitespace_normalize(name) AS name_clean
FROM customers;
```

### Config format

Config is a JSON object with optional keys:

```json
{
    "exact": ["email", "phone"],
    "fuzzy": {"name": 0.85, "address": 0.90},
    "blocking": ["zip"],
    "threshold": 0.85
}
```

## Requirements

- PostgreSQL 15, 16, or 17
- Python 3.11+ with `goldenmatch >= 1.1.0` installed into the server's Python
- `goldenflow` (optional) to enable the `goldenflow_*` transforms

## License

MIT
