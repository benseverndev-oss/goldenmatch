//! Core-API parity functions for the goldenmatch Postgres extension.
//!
//! Mirrors the 13 DuckDB UDFs in
//! `packages/rust/extensions/duckdb/goldenmatch_duckdb/core_apis.py` so the
//! Postgres and DuckDB SQL surfaces expose the same goldenmatch core APIs
//! with an IDENTICAL JSON in / JSON out contract.
//!
//! Each function wraps a `goldenmatch_bridge::api::*` fn (added alongside
//! this module). The bridge handles the pyo3 dispatch + JSON conversion;
//! this module is the SQL surface.
//!
//! ## Conventions (match `quick.rs`)
//! - Table-input functions take a `table_name TEXT` and read it via
//!   `spi::read_table_as_json` (the same `row_to_json` SPI path as
//!   `goldenmatch_dedupe_table`), then forward the records JSON to the bridge.
//! - JSON-in functions take the JSON payloads directly as `TEXT`.
//! - Outputs are JSON `TEXT` (the bridge already does `json.dumps`), except
//!   `goldenmatch_suggest_threshold` which returns `Option<f64>` so it can
//!   emit SQL NULL for unimodal / too-few-scores inputs.
//! - The bridge fns are fail-soft (they return `{"error": ...}` JSON instead
//!   of raising on bad input / optional-dep failure), matching the DuckDB
//!   UDFs. A genuine `BridgeError` (e.g. goldenmatch not importable) still
//!   surfaces via `pgrx::error!`.

use pgrx::prelude::*;

use crate::spi;

// ── Profiling / threshold / domain ──────────────────────────────────────

/// Profile a Postgres table (column stats, types, quality signals).
/// Wraps `goldenmatch.profile_dataframe`. Returns the profile report as JSON.
///
/// ```sql
/// SELECT goldenmatch_profile_table('customers');
/// ```
#[pg_extern]
pub fn goldenmatch_profile_table(table_name: String) -> String {
    let rows_json =
        spi::read_table_as_json(&table_name).unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));
    match goldenmatch_bridge::api::profile_table(&rows_json) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// Suggest an Otsu match-threshold over a JSON array of scores.
/// Wraps `goldenmatch.suggest_threshold`. Returns SQL NULL when the score
/// distribution is unimodal or there are too few scores.
///
/// ```sql
/// SELECT goldenmatch_suggest_threshold('[0.1, 0.2, 0.85, 0.9, 0.95]');
/// ```
#[pg_extern]
pub fn goldenmatch_suggest_threshold(scores_json: String) -> Option<f64> {
    match goldenmatch_bridge::api::suggest_threshold(&scores_json) {
        Ok(value) => value,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// Detect the data domain for a JSON array of column names.
/// Wraps `goldenmatch.core.domain.detect_domain`. Returns the domain profile
/// dataclass as JSON.
///
/// ```sql
/// SELECT goldenmatch_detect_domain('["sku", "product_name", "price"]');
/// ```
#[pg_extern]
pub fn goldenmatch_detect_domain(columns_json: String) -> String {
    match goldenmatch_bridge::api::detect_domain(&columns_json) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// Extract structured features from a free-text value. `kind` selects the
/// extractor: `product`/`electronics` (default), `software`, or
/// `biblio`/`bibliographic`. Wraps the `extract_*_features` functions.
/// Returns the features as JSON.
///
/// ```sql
/// SELECT goldenmatch_extract_features('iPhone 13 Pro 256GB', 'product');
/// ```
#[pg_extern]
pub fn goldenmatch_extract_features(text: String, kind: String) -> String {
    match goldenmatch_bridge::api::extract_features(&text, &kind) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

// ── Evaluation / cluster comparison ─────────────────────────────────────

/// Evaluate predicted pairs/clusters against ground truth.
/// `pairs_json` is a JSON array of `[a, b, score]` triples (-> evaluate_pairs)
/// OR a JSON object `{cluster_id: {"members": [...]}}` (-> evaluate_clusters).
/// `ground_truth_json` is a JSON array of `[a, b]` pairs. Returns the
/// `EvalResult.summary()` (tp/fp/fn/precision/recall/f1/...) as JSON.
///
/// ```sql
/// SELECT goldenmatch_evaluate('[[1,2,0.9],[3,4,0.8]]', '[[1,2]]');
/// ```
#[pg_extern]
pub fn goldenmatch_evaluate(pairs_json: String, ground_truth_json: String) -> String {
    match goldenmatch_bridge::api::evaluate(&pairs_json, &ground_truth_json) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// Compare two clusterings via CCMS. Both args are JSON objects of
/// `{cluster_id: {"members": [...]}}`. Wraps `goldenmatch.compare_clusters`.
/// Returns the `CompareResult.summary()` (TWI + case counts) as JSON.
///
/// ```sql
/// SELECT goldenmatch_compare_clusters(
///     '{"1": {"members": [1, 2]}}',
///     '{"1": {"members": [1, 2, 3]}}'
/// );
/// ```
#[pg_extern]
pub fn goldenmatch_compare_clusters(a_json: String, b_json: String) -> String {
    match goldenmatch_bridge::api::compare_clusters(&a_json, &b_json) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

// ── Data-quality (validate / autofix / anomalies) ───────────────────────

/// Run validation rules over a Postgres table.
/// `rules_json` is a JSON array of rule objects matching the `ValidationRule`
/// dataclass: `{"column", "rule_type", "params", "action"}`. Wraps
/// `goldenmatch.core.validate.validate_dataframe`. Returns
/// `{report, valid_rows, quarantine_rows, quarantine}` as JSON.
///
/// ```sql
/// SELECT goldenmatch_validate_table(
///     'customers',
///     '[{"column": "email", "rule_type": "not_null", "action": "flag"}]'
/// );
/// ```
#[pg_extern]
pub fn goldenmatch_validate_table(table_name: String, rules_json: String) -> String {
    let rows_json =
        spi::read_table_as_json(&table_name).unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));
    match goldenmatch_bridge::api::validate_table(&rows_json, &rules_json) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// Apply auto-fixes to a Postgres table. Wraps `goldenmatch.auto_fix_dataframe`.
/// Returns `{fixes, fixed_rows, rows}` as JSON.
///
/// ```sql
/// SELECT goldenmatch_autofix_table('customers');
/// ```
#[pg_extern]
pub fn goldenmatch_autofix_table(table_name: String) -> String {
    let rows_json =
        spi::read_table_as_json(&table_name).unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));
    match goldenmatch_bridge::api::autofix_table(&rows_json) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// Flag suspicious records in a Postgres table. `sensitivity` is
/// `low`/`medium`/`high` (empty -> `medium`). Wraps
/// `goldenmatch.detect_anomalies`. Returns a JSON array of anomaly dicts.
///
/// ```sql
/// SELECT goldenmatch_detect_anomalies('customers', 'high');
/// ```
#[pg_extern]
pub fn goldenmatch_detect_anomalies(table_name: String, sensitivity: String) -> String {
    let rows_json =
        spi::read_table_as_json(&table_name).unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));
    match goldenmatch_bridge::api::detect_anomalies(&rows_json, &sensitivity) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

// ── AutoConfig verify (preflight / postflight) ──────────────────────────

/// Validate a `(table, config)` pair before a dedupe run.
/// `config_json` is a full `GoldenMatchConfig` JSON. Wraps
/// `goldenmatch.core.autoconfig_verify.preflight`. Returns
/// `{has_errors, config_was_modified, findings}` as JSON.
///
/// ```sql
/// SELECT goldenmatch_preflight('customers', goldenmatch_autoconfig('customers'));
/// ```
#[pg_extern]
pub fn goldenmatch_preflight(table_name: String, config_json: String) -> String {
    let rows_json =
        spi::read_table_as_json(&table_name).unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));
    match goldenmatch_bridge::api::preflight(&rows_json, &config_json) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// Post-run signal report for a `(table, config)` pair. Derives `pair_scores`
/// by running `dedupe_df` on the table with the given config (postflight
/// needs scored pairs not present in the table), then feeds them to
/// `goldenmatch.core.autoconfig_verify.postflight`. Returns
/// `{signals, adjustments, advisories}` as JSON.
///
/// ```sql
/// SELECT goldenmatch_postflight('customers', goldenmatch_autoconfig('customers'));
/// ```
#[pg_extern]
pub fn goldenmatch_postflight(table_name: String, config_json: String) -> String {
    let rows_json =
        spi::read_table_as_json(&table_name).unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));
    match goldenmatch_bridge::api::postflight(&rows_json, &config_json) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

// ── Fellegi-Sunter probabilistic ────────────────────────────────────────

/// Train Fellegi-Sunter m/u probabilities via EM.
/// `rows_json` is a JSON array of record objects (a small training set);
/// `matchkey_json` is a probabilistic `MatchkeyConfig` JSON; `params_json`
/// is an optional JSON object of train_em kwargs (`n_sample_pairs`,
/// `max_iterations`, `convergence`, `seed`, `blocking_fields`; pass `''` or
/// `'{}'` for defaults). Returns the `EMResult` as JSON -- pass it straight
/// to `goldenmatch_score_probabilistic`.
///
/// ```sql
/// SELECT goldenmatch_train_em(
///     '[{"name": "John"}, {"name": "Jon"}]',
///     '{"name": "mk", "type": "probabilistic", "fields": [{"field": "name", "comparison": "jaro_winkler"}]}',
///     '{}'
/// );
/// ```
#[pg_extern]
pub fn goldenmatch_train_em(
    rows_json: String,
    matchkey_json: String,
    params_json: String,
) -> String {
    match goldenmatch_bridge::api::train_em(&rows_json, &matchkey_json, &params_json) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// Score record pairs with trained Fellegi-Sunter probabilities.
/// `rows_json` is a JSON array of record objects (the block to score);
/// `matchkey_json` is the same probabilistic `MatchkeyConfig` used for
/// training; `em_result_json` is the JSON produced by
/// `goldenmatch_train_em`. Returns a JSON array of `[a, b, score]` triples
/// for pairs above the link threshold.
///
/// ```sql
/// SELECT goldenmatch_score_probabilistic(rows_json, matchkey_json, em_json);
/// ```
#[pg_extern]
pub fn goldenmatch_score_probabilistic(
    rows_json: String,
    matchkey_json: String,
    em_result_json: String,
) -> String {
    match goldenmatch_bridge::api::score_probabilistic(&rows_json, &matchkey_json, &em_result_json)
    {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}
