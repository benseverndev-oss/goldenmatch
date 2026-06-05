# goldenmatch-duckdb

[![PyPI](https://img.shields.io/pypi/v/goldenmatch-duckdb?color=d4a017&logo=pypi&logoColor=white)](https://pypi.org/project/goldenmatch-duckdb/)
[![Downloads](https://img.shields.io/pypi/dm/goldenmatch-duckdb?color=2ea44f)](https://pypi.org/project/goldenmatch-duckdb/)
[![Python](https://img.shields.io/pypi/pyversions/goldenmatch-duckdb?logo=python&logoColor=white)](https://pypi.org/project/goldenmatch-duckdb/)

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

### Core-API functions

Thin wrappers over goldenmatch's public core APIs. All return JSON strings
(scalar functions noted otherwise); table-input functions read the named
DuckDB table directly.

| Function | Wraps | Description |
|----------|-------|-------------|
| `goldenmatch_profile_table(table)` | `profile_dataframe` | Full profile report for a table (JSON) |
| `goldenmatch_suggest_threshold(scores_json)` | `suggest_threshold` | Otsu threshold over a JSON score list (DOUBLE; NULL when unimodal) |
| `goldenmatch_detect_domain(columns_json)` | `detect_domain` | Detect data domain from a JSON column-name list |
| `goldenmatch_extract_features(text, kind)` | `extract_product_features` / `extract_software_features` / `extract_biblio_features` | Extract structured features; `kind` = `product`/`electronics`, `software`, or `biblio` |
| `goldenmatch_evaluate(pairs_json, ground_truth_json)` | `evaluate_pairs` / `evaluate_clusters` | Precision/recall/F1 vs. ground truth (auto-selects by shape) |
| `goldenmatch_compare_clusters(a_json, b_json)` | `compare_clusters` | CCMS / TWI comparison of two clusterings |
| `goldenmatch_validate_table(table, rules_json)` | `validate_dataframe` | Apply validation rules; returns report + quarantined rows |
| `goldenmatch_autofix_table(table)` | `auto_fix_dataframe` | Apply common data fixes; returns fixes + fixed rows |
| `goldenmatch_detect_anomalies(table, sensitivity)` | `detect_anomalies` | Flag suspicious records (`low`/`medium`/`high`) |
| `goldenmatch_preflight(table, config_json)` | `preflight` | Pre-run config validation findings |
| `goldenmatch_postflight(table, config_json)` | `postflight` | Post-run signal report (runs dedupe to derive pair scores) |
| `goldenmatch_train_em(rows_json, matchkey_json, params_json)` | `train_em` | Train Fellegi-Sunter m/u probabilities; returns EMResult JSON |
| `goldenmatch_score_probabilistic(rows_json, matchkey_json, em_result_json)` | `score_probabilistic` | Score pairs with a trained EMResult |

```python
# Otsu threshold suggestion
con.sql("SELECT goldenmatch_suggest_threshold('[0.1,0.12,0.9,0.92]')").show()

# Detect domain from columns
con.sql("SELECT goldenmatch_detect_domain('[\"product_title\",\"brand\",\"sku\"]')").show()

# Profile / validate / auto-fix a table
con.sql("SELECT goldenmatch_profile_table('customers')").show()

# Fellegi-Sunter: train, then score
con.sql("""
    SELECT goldenmatch_score_probabilistic(
        :rows, :mk,
        goldenmatch_train_em(:rows, :mk, '{}')
    )
""")
```

### GoldenFlow transforms

8 scalar `VARCHAR -> VARCHAR` UDFs wrapping GoldenFlow's transform registry --
normalize / canonicalize a column before matching. Byte-equivalent to the
Postgres `goldenflow_*` functions, so a transform written for one backend
ports to the other. They fail open (pass the input through unchanged) when
`goldenflow` isn't installed -- `pip install goldenflow` to enable.

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

```python
# Normalize columns inline before matching
con.sql("""
    SELECT
        goldenflow_normalize_email(email)  AS email_norm,
        goldenflow_normalize_phone(phone)  AS phone_e164
    FROM customers
""").show()
```

## Requirements

- Python 3.11+
- DuckDB 1.0+
- goldenmatch >= 1.1.0
