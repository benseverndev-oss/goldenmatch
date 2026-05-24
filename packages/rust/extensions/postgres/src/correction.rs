//! Correction CRUD functions for the goldenmatch Postgres extension
//! (Phase 6A of #437 surface sync, goldenmatch#209).
//!
//! Wraps `goldenmatch_bridge::api::correction_add` + `correction_list`
//! as pgrx `#[pg_extern]` functions. The bridge crate handles the
//! pyo3 dispatch into the Python `MemoryStore`; this module is the
//! SQL surface.
//!
//! ## Function shapes
//!
//! ```sql
//! -- Pair-level (decision in {approve, reject}):
//! SELECT goldenmatch.correction_add(
//!     decision => 'approve',
//!     dataset  => 'customers',
//!     id_a     => 42,
//!     id_b     => 99
//! );
//!
//! -- Field-level (decision = field_correct):
//! SELECT goldenmatch.correction_add(
//!     decision         => 'field_correct',
//!     dataset          => 'customers',
//!     cluster_id       => 42,
//!     field_name       => 'address1',
//!     original_value   => '1 Elm St',
//!     corrected_value  => '1 Elm Street, Apt 4B'
//! );
//!
//! -- Cluster-decision (decision = cluster_decision):
//! SELECT goldenmatch.correction_add(
//!     decision         => 'cluster_decision',
//!     dataset          => 'pub_48',
//!     cluster_id       => 123,
//!     cluster_score    => 0.97,
//!     cluster_outcome  => 'approve'
//! );
//! ```
//!
//! ## Permissions
//!
//! `goldenmatch.correction_add` is REVOKEd from PUBLIC by default in
//! `sql/goldenmatch_pg--0.5.0.sql`. Grant `goldenmatch_correction_writer`
//! (or any role you wire up) EXECUTE privilege before any caller can
//! invoke it. The read-only `goldenmatch.correction_list` is also
//! REVOKEd by default.
//!
//! ## Transactional semantics (known limitation)
//!
//! The Python `MemoryStore` is SQLite-backed by default and commits
//! eagerly. A `BEGIN; SELECT goldenmatch.correction_add(...); ROLLBACK;`
//! sequence does NOT roll back the underlying SQLite write. For
//! transactional semantics, file corrections via the REST or Python
//! paths instead.

use goldenmatch_bridge::api::CorrectionAddArgs;
use goldenmatch_bridge::error::BridgeError;
use pgrx::prelude::*;

/// File a correction. Returns the generated correction UUID as TEXT.
///
/// `decision` must be one of `approve` | `reject` | `field_correct` |
/// `cluster_decision`. Shape-specific required fields:
///
/// - `approve` / `reject`: `id_a` + `id_b` required
/// - `field_correct`: `cluster_id` + `field_name` + `corrected_value` required
/// - `cluster_decision`: `cluster_id` + `cluster_score` + `cluster_outcome` required
///
/// `dataset` is always required and non-empty.
/// `memory_path` defaults to `.goldenmatch/memory.db` (relative to the
/// Postgres data dir at runtime) when NULL.
///
/// All other parameters are optional; pass NULL for unused fields.
#[pg_extern]
#[allow(clippy::too_many_arguments)]
pub fn correction_add(
    decision: String,
    dataset: String,
    id_a: pgrx::default!(Option<i64>, "NULL"),
    id_b: pgrx::default!(Option<i64>, "NULL"),
    cluster_id: pgrx::default!(Option<i64>, "NULL"),
    field_name: pgrx::default!(Option<String>, "NULL"),
    original_value: pgrx::default!(Option<String>, "NULL"),
    corrected_value: pgrx::default!(Option<String>, "NULL"),
    cluster_score: pgrx::default!(Option<f64>, "NULL"),
    cluster_outcome: pgrx::default!(Option<String>, "NULL"),
    reason: pgrx::default!(Option<String>, "NULL"),
    matchkey_name: pgrx::default!(Option<String>, "NULL"),
    source: pgrx::default!(Option<String>, "NULL"),
    memory_path: pgrx::default!(Option<String>, "NULL"),
) -> String {
    // Convert Option<String> to Option<&str> for the bridge args
    // (CorrectionAddArgs holds borrowed string refs).
    let field_name_s = field_name.as_deref();
    let original_value_s = original_value.as_deref();
    let corrected_value_s = corrected_value.as_deref();
    let cluster_outcome_s = cluster_outcome.as_deref();
    let reason_s = reason.as_deref();
    let matchkey_name_s = matchkey_name.as_deref();
    let source_s = source.as_deref();
    let memory_path_s = memory_path.as_deref();

    let args = CorrectionAddArgs {
        decision: &decision,
        dataset: &dataset,
        source: source_s,
        memory_path: memory_path_s,
        reason: reason_s,
        matchkey_name: matchkey_name_s,
        id_a,
        id_b,
        original_score: None,
        cluster_id,
        field_name: field_name_s,
        original_value: original_value_s,
        corrected_value: corrected_value_s,
        cluster_score,
        cluster_outcome: cluster_outcome_s,
    };

    match goldenmatch_bridge::api::correction_add(args) {
        Ok(uuid) => uuid,
        Err(BridgeError::Validation(msg)) => {
            // Validation errors get a distinctive SQL state so callers
            // can distinguish "you passed the wrong shape" from
            // "MemoryStore exploded".
            pgrx::error!("goldenmatch correction_add validation: {}", msg);
        }
        Err(e) => pgrx::error!("goldenmatch correction_add: {}", e),
    }
}

/// List corrections for a dataset (or all when NULL). Returns a JSON
/// array of correction dicts.
#[pg_extern]
pub fn correction_list(
    dataset: pgrx::default!(Option<String>, "NULL"),
    memory_path: pgrx::default!(Option<String>, "NULL"),
) -> String {
    let dataset_s = dataset.as_deref();
    let memory_path_s = memory_path.as_deref();
    match goldenmatch_bridge::api::correction_list(dataset_s, memory_path_s) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch correction_list: {}", e),
    }
}
