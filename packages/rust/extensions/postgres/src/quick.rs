//! Quick-start SQL functions for GoldenMatch.
//!
//! Two flavors of each function:
//! - Table-based: reads from a PG table via SPI (primary interface)
//! - JSON-based: accepts raw JSON (for programmatic use)

use pgrx::prelude::*;

use crate::spi;

// ── Table-based functions (primary interface) ──────────────────────────

/// Deduplicate a Postgres table. Returns JSON with golden records and stats.
#[pg_extern]
pub fn goldenmatch_dedupe_table(table_name: String, config_json: String) -> String {
    let rows_json =
        spi::read_table_as_json(&table_name).unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));

    match goldenmatch_bridge::api::dedupe(&rows_json, &config_json) {
        Ok(result) => result.golden_json.unwrap_or_else(|| result.stats_json),
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// Match a target table against a reference table.
#[pg_extern]
pub fn goldenmatch_match_tables(
    target_table: String,
    reference_table: String,
    config_json: String,
) -> String {
    let target_json = spi::read_table_as_json(&target_table)
        .unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));
    let ref_json = spi::read_table_as_json(&reference_table)
        .unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));

    match goldenmatch_bridge::api::match_tables(&target_json, &ref_json, &config_json) {
        Ok(result) => result.matched_json.unwrap_or_else(|| "[]".to_string()),
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

// ── Table-returning functions (structured results) ─────────────────────

/// Deduplicate a table and return matched pairs as rows.
///
/// ```sql
/// SELECT * FROM goldenmatch_dedupe_pairs('customers', '{"exact": ["email"]}');
/// ```
#[pg_extern]
pub fn goldenmatch_dedupe_pairs(
    table_name: String,
    config_json: String,
) -> TableIterator<'static, (name!(id_a, i64), name!(id_b, i64), name!(score, f64))> {
    let rows_json =
        spi::read_table_as_json(&table_name).unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));

    match goldenmatch_bridge::api::dedupe_pairs(&rows_json, &config_json) {
        Ok(pairs) => {
            let rows: Vec<(i64, i64, f64)> = pairs
                .into_iter()
                .map(|p| (p.id_a, p.id_b, p.score))
                .collect();
            TableIterator::new(rows)
        }
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// Deduplicate a table and return cluster assignments as rows.
///
/// ```sql
/// SELECT * FROM goldenmatch_dedupe_clusters('customers', '{"exact": ["email"]}');
/// ```
#[pg_extern]
pub fn goldenmatch_dedupe_clusters(
    table_name: String,
    config_json: String,
) -> TableIterator<
    'static,
    (
        name!(cluster_id, i64),
        name!(record_id, i64),
        name!(cluster_size, i64),
    ),
> {
    let rows_json =
        spi::read_table_as_json(&table_name).unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));

    match goldenmatch_bridge::api::dedupe_clusters(&rows_json, &config_json) {
        Ok(members) => {
            let rows: Vec<(i64, i64, i64)> = members
                .into_iter()
                .map(|m| (m.cluster_id, m.record_id, m.cluster_size))
                .collect();
            TableIterator::new(rows)
        }
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

// ── Scalar functions ───────────────────────────────────────────────────

/// Score two strings using a named similarity algorithm.
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
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// Score a pair of records represented as JSON objects.
#[pg_extern]
pub fn goldenmatch_score_pair(record_a: String, record_b: String, config: String) -> f64 {
    match goldenmatch_bridge::api::score_pair(&record_a, &record_b, &config) {
        Ok(score) => score,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// Explain why two records match (or don't) in natural language.
#[pg_extern]
pub fn goldenmatch_explain(record_a: String, record_b: String, config: String) -> String {
    match goldenmatch_bridge::api::explain_pair(&record_a, &record_b, &config) {
        Ok(explanation) => explanation,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

// ── JSON-based functions (programmatic use) ────────────────────────────

/// Deduplicate JSON records directly.
#[pg_extern]
pub fn goldenmatch_dedupe(rows_json: String, config_json: String) -> String {
    match goldenmatch_bridge::api::dedupe(&rows_json, &config_json) {
        Ok(result) => result.golden_json.unwrap_or_else(|| result.stats_json),
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// Match two sets of JSON records.
#[pg_extern]
pub fn goldenmatch_match(
    target_json: String,
    reference_json: String,
    config_json: String,
) -> String {
    match goldenmatch_bridge::api::match_tables(&target_json, &reference_json, &config_json) {
        Ok(result) => result.matched_json.unwrap_or_else(|| "[]".to_string()),
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

// ── AutoConfig + telemetry (v1.7-v1.12 surface) ────────────────────────

/// Run AutoConfigController on a table and return the committed config JSON.
///
/// Pipe the output into `goldenmatch_dedupe_full(table, <result>)` to run the
/// pipeline with the auto-configured shape. The committed config is the same
/// `GoldenMatchConfig` JSON the CLI `goldenmatch autoconfig` would write to
/// disk, including any `negative_evidence` (Path Y) fields the controller
/// added.
///
/// To inspect what the controller decided, pair with `goldenmatch_autoconfig_telemetry()`
/// — the two functions share the same telemetry blob; this one returns only
/// the config payload.
///
/// ```sql
/// SELECT goldenmatch_autoconfig('customers');
/// ```
#[pg_extern]
pub fn goldenmatch_autoconfig(table_name: String) -> String {
    let rows_json =
        spi::read_table_as_json(&table_name).unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));
    match goldenmatch_bridge::api::autoconfig(&rows_json) {
        Ok(result) => result.config_json,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// Run AutoConfigController and return the telemetry JSON (stop_reason,
/// health verdict, refit decisions, indicator column priors, committed NE).
///
/// Same shape as the web UI's `/api/v1/controller/telemetry` endpoint.
/// Run alongside `goldenmatch_autoconfig()` when you want to inspect WHY the
/// controller picked what it did — typical SQL pattern is:
///
/// ```sql
/// WITH cfg AS (SELECT goldenmatch_autoconfig('customers') AS json),
///      tel AS (SELECT goldenmatch_autoconfig_telemetry('customers') AS json)
/// SELECT (SELECT json FROM cfg) AS config, (SELECT json FROM tel) AS telemetry;
/// ```
///
/// Note: this re-runs the controller. For a single-shot variant that
/// returns both, persist the result of one call to a temp table.
#[pg_extern]
pub fn goldenmatch_autoconfig_telemetry(table_name: String) -> String {
    let rows_json =
        spi::read_table_as_json(&table_name).unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));
    match goldenmatch_bridge::api::autoconfig(&rows_json) {
        Ok(result) => result.telemetry_json,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// Deduplicate a Postgres table using a *full* `GoldenMatchConfig` JSON.
///
/// Unlike `goldenmatch_dedupe_table`, which forwards only the slim
/// `exact`/`fuzzy`/`blocking`/`threshold` keys, this accepts the full
/// Pydantic shape — including `negative_evidence` (Path Y), per-matchkey
/// `comparison` / `scorer` / `weight`, `standardization`, `golden_rules`,
/// and so on. Use this when you've already obtained a committed config from
/// `goldenmatch_autoconfig()` and want to apply it unchanged.
#[pg_extern]
pub fn goldenmatch_dedupe_full(table_name: String, config_json: String) -> String {
    let rows_json =
        spi::read_table_as_json(&table_name).unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));
    match goldenmatch_bridge::api::dedupe_full(&rows_json, &config_json) {
        Ok(result) => result.golden_json.unwrap_or_else(|| result.stats_json),
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// Deduplicate a table with the full config and return the controller telemetry.
///
/// Useful for the "auto-configure once, run with telemetry, store both" flow
/// without re-running the controller.
#[pg_extern]
pub fn goldenmatch_dedupe_full_telemetry(table_name: String, config_json: String) -> String {
    let rows_json =
        spi::read_table_as_json(&table_name).unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));
    match goldenmatch_bridge::api::dedupe_full(&rows_json, &config_json) {
        Ok(result) => result
            .telemetry_json
            .unwrap_or_else(|| "{\"available\":false}".to_string()),
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}
