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
    let table_data =
        spi::read_table(&table_name).unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));

    match goldenmatch_bridge::api::dedupe(&table_data, &config_json) {
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
    let target_data =
        spi::read_table(&target_table).unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));
    let ref_data =
        spi::read_table(&reference_table).unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));

    match goldenmatch_bridge::api::match_tables(&target_data, &ref_data, &config_json) {
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
    let table_data =
        spi::read_table(&table_name).unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));

    match goldenmatch_bridge::api::dedupe_pairs(&table_data, &config_json) {
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
    let table_data =
        spi::read_table(&table_name).unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));

    match goldenmatch_bridge::api::dedupe_clusters(&table_data, &config_json) {
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

/// Match a target table against a reference table and return the matched
/// pairs as rows: each target row linked to a reference row with its score.
///
/// `target_id` / `reference_id` are 0-based row indices into the respective
/// input tables (the bridge normalizes match_df's combined `__row_id__`
/// space back to per-table indices).
///
/// ```sql
/// SELECT * FROM goldenmatch_match_pairs('incoming', 'master', '{}');
/// ```
#[pg_extern]
pub fn goldenmatch_match_pairs(
    target_table: String,
    reference_table: String,
    config_json: String,
) -> TableIterator<
    'static,
    (
        name!(target_id, i64),
        name!(reference_id, i64),
        name!(score, f64),
    ),
> {
    let target_data =
        spi::read_table(&target_table).unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));
    let ref_data =
        spi::read_table(&reference_table).unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));

    match goldenmatch_bridge::api::match_pairs(&target_data, &ref_data, &config_json) {
        Ok(pairs) => {
            let rows: Vec<(i64, i64, f64)> = pairs
                .into_iter()
                .map(|p| (p.target_id, p.reference_id, p.score))
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
///
/// The four rapidfuzz-family scorers (jaro_winkler / levenshtein / token_sort /
/// exact) run **native-direct** over the pyo3-free `score-core` kernel — no
/// embedded-CPython round-trip per row, which matters for `WHERE
/// goldenmatch_score(...) > t` over a large table. `score-core` IS the reference
/// the Python path uses, so results are unchanged. Any other scorer
/// (soundex_match / ensemble / future) falls back to the bridge, so nothing is
/// lost.
#[pg_extern]
pub fn goldenmatch_score(
    value_a: String,
    value_b: String,
    scorer: default!(Option<String>, "'jaro_winkler'"),
) -> f64 {
    let scorer_name = scorer.unwrap_or_else(|| "jaro_winkler".to_string());

    // Native fast path: score-core's scorer ids (0=jw, 1=lev, 2=token_sort,
    // 3=exact) match the bridge's named scorers byte-for-algorithm.
    let native_id = match scorer_name.as_str() {
        "jaro_winkler" => Some(0u8),
        "levenshtein" => Some(1),
        "token_sort" => Some(2),
        "exact" => Some(3),
        _ => None,
    };
    if let Some(id) = native_id {
        return goldenmatch_score_core::score_one(id, &value_a, &value_b);
    }

    // Bridge fallback for scorers score-core doesn't implement.
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
    // JSON-direct entry: the caller hands records as JSON, so wrap them for the
    // TableData dispatch (the columnar path only applies to SPI table reads).
    let table_data = goldenmatch_bridge::convert::TableData::Json(rows_json);
    match goldenmatch_bridge::api::dedupe(&table_data, &config_json) {
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
    // JSON-direct entry: wrap each record set for the TableData dispatch (the
    // columnar path only applies to SPI table reads).
    let target_data = goldenmatch_bridge::convert::TableData::Json(target_json);
    let reference_data = goldenmatch_bridge::convert::TableData::Json(reference_json);
    match goldenmatch_bridge::api::match_tables(&target_data, &reference_data, &config_json) {
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
/// `mode` selects the auto-config strategy: `'standard'` (the default,
/// iterative AutoConfigController) or `'probabilistic'` (Fellegi-Sunter
/// matchkeys). The SQL `DEFAULT 'standard'` keeps the 1-arg call working.
///
/// ```sql
/// SELECT goldenmatch_autoconfig('customers');
/// SELECT goldenmatch_autoconfig('customers', 'probabilistic');
/// ```
#[pg_extern]
pub fn goldenmatch_autoconfig(table_name: String, mode: String) -> String {
    let table_data =
        spi::read_table(&table_name).unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));
    match goldenmatch_bridge::api::autoconfig(&table_data, &mode) {
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
    let table_data =
        spi::read_table(&table_name).unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));
    match goldenmatch_bridge::api::autoconfig(&table_data, "standard") {
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
    let table_data =
        spi::read_table(&table_name).unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));
    match goldenmatch_bridge::api::dedupe_full(&table_data, &config_json) {
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
    let table_data =
        spi::read_table(&table_name).unwrap_or_else(|e| pgrx::error!("goldenmatch: {}", e));
    match goldenmatch_bridge::api::dedupe_full(&table_data, &config_json) {
        Ok(result) => result
            .telemetry_json
            .unwrap_or_else(|| "{\"available\":false}".to_string()),
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

// ── Identity Graph (v2.0) ────────────────────────────────────────────────
//
// Contract: docs/superpowers/specs/2026-05-12-identity-graph-duckdb-contract.md
// All identity functions accept the path to the identity SQLite/Postgres
// store as an explicit second arg. To target a Postgres backend, pass the
// libpq DSN; for SQLite pass the filesystem path. Empty-string filters
// (``dataset``, ``status``) mean "no filter on that dimension".

/// Resolve the store reference the identity read functions hand to the bridge.
///
/// A non-empty `db_path` is used verbatim (a SQLite file path, or a libpq DSN
/// the bridge routes to the Postgres backend). An EMPTY `db_path` (#1913 P2)
/// means "the in-DB dataset": substitute the `goldenmatch.identity_dsn` GUC
/// (or the `GOLDENMATCH_IDENTITY_DSN` / `GOLDENMATCH_DATABASE_URL` env) so the
/// read surface serves the same Postgres identity graph `gm_resolve` writes.
/// Empty is the sentinel because these functions are STRICT (SQL NULL never
/// reaches them) and already treat empty `dataset`/`status` as "unset".
fn identity_store_ref(db_path: String) -> String {
    if !db_path.trim().is_empty() {
        return db_path;
    }
    crate::pipeline::resolve_identity_dsn().unwrap_or_else(|| {
        pgrx::error!(
            "goldenmatch: identity read needs a db_path (SQLite path or DSN), \
             or set `goldenmatch.identity_dsn` (or GOLDENMATCH_IDENTITY_DSN / \
             GOLDENMATCH_DATABASE_URL) to serve the in-DB dataset"
        )
    })
}

/// Resolve a record_id (form: ``{source}:{source_pk}``) to its identity view.
/// Returns ``{"found": false}`` when the record has no identity. Empty
/// ``db_path`` reads the in-DB Postgres dataset (#1913 P2).
#[pg_extern]
pub fn goldenmatch_identity_resolve(record_id: String, db_path: String) -> String {
    let store_ref = identity_store_ref(db_path);
    match goldenmatch_bridge::api::identity_resolve(&record_id, &store_ref) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// Return the full identity view JSON for ``entity_id``. Empty ``db_path``
/// reads the in-DB Postgres dataset (#1913 P2).
#[pg_extern]
pub fn goldenmatch_identity_view(entity_id: String, db_path: String) -> String {
    let store_ref = identity_store_ref(db_path);
    match goldenmatch_bridge::api::identity_view(&entity_id, &store_ref) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// Return the temporal event log for an identity as a JSON array. Empty
/// ``db_path`` reads the in-DB Postgres dataset (#1913 P2).
#[pg_extern]
pub fn goldenmatch_identity_history(entity_id: String, db_path: String) -> String {
    let store_ref = identity_store_ref(db_path);
    match goldenmatch_bridge::api::identity_history(&entity_id, &store_ref) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// List ``conflicts_with`` evidence edges as a JSON array. Empty ``dataset``
/// returns conflicts across all datasets; empty ``db_path`` reads the in-DB
/// Postgres dataset (#1913 P2).
#[pg_extern]
pub fn goldenmatch_identity_conflicts(dataset: String, db_path: String) -> String {
    let store_ref = identity_store_ref(db_path);
    match goldenmatch_bridge::api::identity_conflicts(&dataset, &store_ref) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// List identities filtered by ``dataset`` / ``status`` (empty = no filter).
/// Empty ``db_path`` reads the in-DB Postgres dataset (#1913 P2).
#[pg_extern]
pub fn goldenmatch_identity_list(dataset: String, status: String, db_path: String) -> String {
    let store_ref = identity_store_ref(db_path);
    match goldenmatch_bridge::api::identity_list(&dataset, &status, &store_ref) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

// ── Identity audit / MDM reads (post-#1913) ──────────────────────────────
//
// Audit chain (`gm_identity_audit` / `_audit_verify`) + MDM operator views
// (`gm_identity_profile` / `_stats` / `_worklist`). Reads, so they take the
// same `db_path` store ref as the `goldenmatch_identity_*` reads above (empty
// → the in-DB Postgres dataset via `goldenmatch.identity_dsn`). Empty
// `dataset` = no dataset filter.

/// Append-only audit-log page (JSON `{"items": [...], "total": n}`). Empty
/// ``dataset`` = every dataset; empty ``db_path`` reads the in-DB dataset.
#[pg_extern]
pub fn gm_identity_audit(dataset: String, db_path: String) -> String {
    let store_ref = identity_store_ref(db_path);
    match goldenmatch_bridge::api::identity_audit(&store_ref, &dataset) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// Replay the seal chain + content hashes and report integrity (JSON verdict).
/// Empty ``db_path`` reads the in-DB dataset.
#[pg_extern]
pub fn gm_identity_audit_verify(dataset: String, db_path: String) -> String {
    let store_ref = identity_store_ref(db_path);
    match goldenmatch_bridge::api::identity_audit_verify(&store_ref, &dataset) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// Full MDM profile of one entity (JSON), or ``{"found": false}`` when absent.
/// Empty ``db_path`` reads the in-DB dataset.
#[pg_extern]
pub fn gm_identity_profile(entity_id: String, db_path: String) -> String {
    let store_ref = identity_store_ref(db_path);
    match goldenmatch_bridge::api::identity_profile(&store_ref, &entity_id) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// Graph-level identity health summary (JSON). Empty ``dataset`` = whole graph;
/// empty ``db_path`` reads the in-DB dataset.
#[pg_extern]
pub fn gm_identity_stats(dataset: String, db_path: String) -> String {
    let store_ref = identity_store_ref(db_path);
    match goldenmatch_bridge::api::identity_stats(&store_ref, &dataset) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}

/// Prioritized steward worklist (JSON `{"items": [...]}`) — active entities with
/// open conflicts and/or weak confidence. Empty ``dataset`` = all; empty
/// ``db_path`` reads the in-DB dataset.
#[pg_extern]
pub fn gm_identity_worklist(dataset: String, db_path: String) -> String {
    let store_ref = identity_store_ref(db_path);
    match goldenmatch_bridge::api::identity_worklist(&store_ref, &dataset) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch: {}", e),
    }
}
