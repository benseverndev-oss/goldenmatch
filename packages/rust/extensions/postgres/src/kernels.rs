//! Native Core kernel + local-embedding SQL functions for the goldenmatch
//! Postgres extension (#509 — DuckDB<->Postgres lockstep with `core_kernels.py`).
//!
//! Wraps `goldenmatch_bridge::api::{connected_components, pair_dedup, embed_local}`
//! as pgrx `#[pg_extern]` functions. The bridge handles pyo3 dispatch into
//! `goldenmatch.native` (Rust kernels when built, else pure-Python) and the
//! local in-house embedder; this module is the SQL surface.
//!
//! ```sql
//! SELECT goldenmatch.goldenmatch_connected_components('[[1,2,0.9],[2,3,0.8]]');
//! SELECT goldenmatch.goldenmatch_pair_dedup('[[2,1,0.5],[1,2,0.9]]');
//! SELECT goldenmatch.goldenmatch_embed_local('John Smith', '/path/to/model');
//! ```
//!
//! JSON in / JSON out; the bridge functions are fail-soft (`{"error": ...}`),
//! so a malformed call returns an error JSON rather than aborting the query.
use pgrx::prelude::*;

/// Connected components over JSON `[[a, b, score], ...]` pairs. Returns a JSON
/// array of components.
#[pg_extern]
pub fn goldenmatch_connected_components(pairs_json: String) -> String {
    match goldenmatch_bridge::api::connected_components(&pairs_json) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch_connected_components: {}", e),
    }
}

/// Canonicalize + keep max score per pair. Returns a JSON array of `[a, b, score]`.
#[pg_extern]
pub fn goldenmatch_pair_dedup(pairs_json: String) -> String {
    match goldenmatch_bridge::api::pair_dedup(&pairs_json) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch_pair_dedup: {}", e),
    }
}

/// Embed one text with the local in-house embedder. `model_path` is a saved
/// `GoldenEmbedModel` directory. Returns a JSON float array.
#[pg_extern]
pub fn goldenmatch_embed_local(text: String, model_path: String) -> String {
    match goldenmatch_bridge::api::embed_local(&text, &model_path) {
        Ok(json) => json,
        Err(e) => pgrx::error!("goldenmatch_embed_local: {}", e),
    }
}

/// Canonical record fingerprint (64 lowercase hex) of a JSON record object.
/// The cross-surface stable record-id hash — same value the DuckDB
/// `goldenmatch_record_fingerprint` UDF, the native C ABI, and the Python
/// identity path produce. `__`-prefixed keys are dropped.
///
/// Computed **in pure Rust** via `goldenmatch-fingerprint-core` — NOT through
/// the embedded-CPython bridge. This is the first SQL function that needs no
/// interpreter for its work (the decoupling lever).
///
/// ```sql
/// SELECT goldenmatch.goldenmatch_record_fingerprint('{"first":"Alex","last":"Smith"}');
/// ```
#[pg_extern]
pub fn goldenmatch_record_fingerprint(record_json: String) -> String {
    match goldenmatch_fingerprint_core::fingerprint_json(&record_json) {
        Ok(hex) => hex,
        Err(e) => pgrx::error!("goldenmatch_record_fingerprint: {}", e),
    }
}

#[cfg(any(test, feature = "pg_test"))]
#[pgrx::pg_schema]
mod tests {
    use pgrx::prelude::*;

    /// pgrx computes the canonical fingerprint in pure Rust; assert it matches
    /// the pinned vector shared with the Python + native + DuckDB surfaces.
    #[pg_test]
    fn record_fingerprint_matches_pinned() {
        let got = crate::kernels::goldenmatch_record_fingerprint(r#"{"a":"x"}"#.to_string());
        assert_eq!(
            got,
            "7381d5ba2dac5be0af49232a3209ab8d0dc2e4ed804a60ce533fdfe5254307e3"
        );
    }
}
