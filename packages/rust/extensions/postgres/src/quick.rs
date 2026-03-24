//! Quick-start SQL functions for GoldenMatch.
//!
//! Two flavors of each function:
//! - Table-based: reads from a PG table via SPI (primary interface)
//! - JSON-based: accepts raw JSON (for programmatic use)

use pgrx::prelude::*;

use crate::spi;

// ── Table-based functions (primary interface) ──────────────────────────

/// Deduplicate a Postgres table.
///
/// ```sql
/// SELECT goldenmatch_dedupe_table('customers', '{"exact": ["email"]}');
/// -- Returns JSON with golden records, clusters, and stats
/// ```
#[pg_extern]
pub fn goldenmatch_dedupe_table(table_name: String, config_json: String) -> String {
    let rows_json = match spi::read_table_as_json(&table_name) {
        Ok(json) => json,
        Err(e) => return format!("{{\"error\": \"{}\"}}", e),
    };

    match goldenmatch_bridge::api::dedupe(&rows_json, &config_json) {
        Ok(result) => result.golden_json.unwrap_or_else(|| result.stats_json),
        Err(e) => format!("{{\"error\": \"{}\"}}", e),
    }
}

/// Match a target table against a reference table.
///
/// ```sql
/// SELECT goldenmatch_match_tables('prospects', 'customers', '{"fuzzy": {"name": 0.85}}');
/// -- Returns JSON with matched pairs
/// ```
#[pg_extern]
pub fn goldenmatch_match_tables(
    target_table: String,
    reference_table: String,
    config_json: String,
) -> String {
    let target_json = match spi::read_table_as_json(&target_table) {
        Ok(json) => json,
        Err(e) => return format!("{{\"error\": \"{}\"}}", e),
    };
    let ref_json = match spi::read_table_as_json(&reference_table) {
        Ok(json) => json,
        Err(e) => return format!("{{\"error\": \"{}\"}}", e),
    };

    match goldenmatch_bridge::api::match_tables(&target_json, &ref_json, &config_json) {
        Ok(result) => result.matched_json.unwrap_or_else(|| "[]".to_string()),
        Err(e) => format!("{{\"error\": \"{}\"}}", e),
    }
}

// ── Scalar functions ───────────────────────────────────────────────────

/// Score two strings using a named similarity algorithm.
///
/// ```sql
/// SELECT goldenmatch_score('John Smith', 'Jon Smyth', 'jaro_winkler');
/// -- Returns: 0.91
/// ```
///
/// Supported scorers: jaro_winkler, levenshtein, exact, token_sort, soundex_match
#[pg_extern]
pub fn goldenmatch_score(
    value_a: String,
    value_b: String,
    scorer: default!(Option<String>, "'jaro_winkler'"),
) -> f64 {
    let scorer_name = scorer.unwrap_or_else(|| "jaro_winkler".to_string());

    match goldenmatch_bridge::api::score_strings(&value_a, &value_b, &scorer_name) {
        Ok(score) => score,
        Err(e) => {
            pgrx::warning!("goldenmatch_score error: {}", e);
            0.0
        }
    }
}

/// Score a pair of records represented as JSON objects.
///
/// ```sql
/// SELECT goldenmatch_score_pair(
///     '{"name": "John Smith", "email": "j@x.com"}',
///     '{"name": "Jon Smyth", "email": "j@x.com"}',
///     '{"fuzzy": {"name": 0.85}, "exact": ["email"]}'
/// );
/// ```
#[pg_extern]
pub fn goldenmatch_score_pair(record_a: String, record_b: String, config: String) -> f64 {
    match goldenmatch_bridge::api::score_pair(&record_a, &record_b, &config) {
        Ok(score) => score,
        Err(e) => {
            pgrx::warning!("goldenmatch_score_pair error: {}", e);
            0.0
        }
    }
}

/// Explain why two records match (or don't) in natural language.
///
/// ```sql
/// SELECT goldenmatch_explain(
///     '{"name": "John Smith", "email": "j@x.com"}',
///     '{"name": "Jon Smyth", "email": "j@x.com"}',
///     '{"fuzzy": {"name": 0.85}, "exact": ["email"]}'
/// );
/// ```
#[pg_extern]
pub fn goldenmatch_explain(record_a: String, record_b: String, config: String) -> String {
    match goldenmatch_bridge::api::explain_pair(&record_a, &record_b, &config) {
        Ok(explanation) => explanation,
        Err(e) => format!("Error: {}", e),
    }
}

// ── JSON-based functions (programmatic use) ────────────────────────────

/// Deduplicate JSON records directly.
///
/// ```sql
/// SELECT goldenmatch_dedupe(
///     '[{"name": "John", "email": "j@x.com"}, {"name": "JOHN", "email": "j@x.com"}]',
///     '{"exact": ["email"]}'
/// );
/// ```
#[pg_extern]
pub fn goldenmatch_dedupe(rows_json: String, config_json: String) -> String {
    match goldenmatch_bridge::api::dedupe(&rows_json, &config_json) {
        Ok(result) => result.golden_json.unwrap_or_else(|| result.stats_json),
        Err(e) => format!("{{\"error\": \"{}\"}}", e),
    }
}

/// Match two sets of JSON records.
///
/// ```sql
/// SELECT goldenmatch_match(
///     '[{"name": "John", "email": "j@x.com"}]',
///     '[{"name": "JOHN SMITH", "email": "j@x.com"}]',
///     '{"exact": ["email"]}'
/// );
/// ```
#[pg_extern]
pub fn goldenmatch_match(
    target_json: String,
    reference_json: String,
    config_json: String,
) -> String {
    match goldenmatch_bridge::api::match_tables(&target_json, &reference_json, &config_json) {
        Ok(result) => result.matched_json.unwrap_or_else(|| "[]".to_string()),
        Err(e) => format!("{{\"error\": \"{}\"}}", e),
    }
}
