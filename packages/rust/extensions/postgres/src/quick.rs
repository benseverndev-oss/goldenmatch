//! Quick-start SQL functions for GoldenMatch.
//!
//! These are registered in the `public` schema for easy access.
//! Each function is a thin wrapper around the bridge crate's API.

use pgrx::prelude::*;

/// Score two strings using a named similarity algorithm.
///
/// ```sql
/// SELECT goldenmatch_score('John Smith', 'Jon Smyth', 'jaro_winkler');
/// -- Returns: 0.91
///
/// SELECT goldenmatch_score('hello', 'hello', 'exact');
/// -- Returns: 1.0
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
/// -- Returns: 0.95
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
/// -- Returns: 'MATCH (score: 0.93) ...'
/// ```
#[pg_extern]
pub fn goldenmatch_explain(record_a: String, record_b: String, config: String) -> String {
    match goldenmatch_bridge::api::explain_pair(&record_a, &record_b, &config) {
        Ok(explanation) => explanation,
        Err(e) => format!("Error: {}", e),
    }
}

/// Deduplicate a table and return golden records as JSON.
///
/// ```sql
/// SELECT * FROM goldenmatch_dedupe(
///     '[{"name": "John", "email": "j@x.com"}, {"name": "JOHN", "email": "j@x.com"}]',
///     '{"exact": ["email"]}'
/// );
/// ```
///
/// Note: This initial version accepts JSON input directly.
/// Future versions will read from a named table via SPI.
#[pg_extern]
pub fn goldenmatch_dedupe(rows_json: String, config_json: String) -> String {
    match goldenmatch_bridge::api::dedupe(&rows_json, &config_json) {
        Ok(result) => {
            // Return golden records JSON, or stats if no golden records
            result.golden_json.unwrap_or_else(|| result.stats_json)
        }
        Err(e) => format!("{{\"error\": \"{}\"}}", e),
    }
}

/// Match records from a target table against a reference table.
///
/// ```sql
/// SELECT goldenmatch_match(
///     '[{"name": "John", "email": "j@x.com"}]',
///     '[{"name": "JOHN SMITH", "email": "j@x.com"}]',
///     '{"exact": ["email"]}'
/// );
/// ```
///
/// Note: This initial version accepts JSON input directly.
/// Future versions will read from named tables via SPI.
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
