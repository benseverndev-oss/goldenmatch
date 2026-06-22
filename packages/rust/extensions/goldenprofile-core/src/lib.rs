//! `goldenprofile-core` -- pyo3-free Virtual Fingerprint / entity-profiling
//! engine.
//!
//! The "Semantic Signature engine": resolve graph elements (nodes AND edges)
//! across documents by comparing rigid, LLM-synthesized profiles instead of raw
//! extraction or neighborhood text. Built to repair the MuSiQue multi-hop graph
//! shatter -- see `score.rs` for the Row-3/Row-4 reasoning that motivates the
//! whole design.
//!
//! Layering (each module has the full rationale in its header):
//! - [`model`]     -- the rigid `name | category | anchor | attribute` schema.
//! - [`signature`] -- structured (canonical-hash) + semantic (SimHash) blocking.
//! - [`score`]     -- the anti-shatter fusion scorer.
//! - [`resolve`]   -- block -> score -> WCC cluster, the end-to-end entry point.
//!
//! Intentionally pyo3-free, Arrow-free, LLM-free, and embedding-model-free: the
//! host synthesizes the profiles (LLM) and supplies the fingerprint embeddings
//! (goldenembed), exactly as `goldengraph-core` keeps the LLM in the Python
//! host. The pyo3 binding is `goldenprofile-native`; the wasm-bindgen + C ABI
//! bindings are `goldenprofile-wasm` / `goldenprofile-cabi`. Every signal is
//! reused from an existing shared core, so the engine is byte-identical across
//! surfaces by construction.

pub mod model;
pub mod resolve;
pub mod score;
pub mod signature;

use serde::Deserialize;

pub use model::{ElementKind, Profile};
pub use resolve::{resolve, Resolution, ResolveConfig, ResolvedEdge};
pub use score::{cosine01, name_similarity, score_pair, PairScore, ScoreConfig};
pub use signature::{candidate_pairs, semantic_band_keys, structured_block_keys};

/// The JSON request every binding (pyo3 / wasm / C ABI) marshals. `embeddings`
/// and `config` are optional -- omit `embeddings` for structured-only
/// resolution, omit `config` (or any field of it) to take the zero-config
/// defaults. Keeping ONE boundary here is what makes the engine byte-identical
/// across surfaces.
#[derive(Debug, Deserialize)]
pub struct ResolveRequest {
    pub profiles: Vec<Profile>,
    #[serde(default)]
    pub embeddings: Vec<Vec<f64>>,
    #[serde(default)]
    pub config: ResolveConfig,
}

/// Parse a [`ResolveRequest`], resolve, and serialize the [`Resolution`]. The
/// single marshaling boundary shared by every binding. Errors are returned as
/// strings (each binding maps them to its own error type).
pub fn resolve_json(request: &str) -> Result<String, String> {
    let req: ResolveRequest = serde_json::from_str(request).map_err(|e| e.to_string())?;
    if !req.embeddings.is_empty() && req.embeddings.len() != req.profiles.len() {
        return Err(format!(
            "embeddings length {} != profiles length {} (supply one embedding per profile, or none)",
            req.embeddings.len(),
            req.profiles.len()
        ));
    }
    let res = resolve(&req.profiles, &req.embeddings, &req.config);
    serde_json::to_string(&res).map_err(|e| e.to_string())
}

#[cfg(test)]
mod smoke {
    use super::*;

    #[test]
    fn crate_builds() {
        assert_eq!(2 + 2, 4);
    }

    #[test]
    fn resolve_json_roundtrip() {
        let req = r#"{
            "profiles": [
                {"kind":"node","name":"Thomas Nabbes","category":"Playwright","anchor":"17th c","attribute":"Wrote Play X"},
                {"kind":"node","name":"Nabbes","category":"Playwright","anchor":"UNKNOWN","attribute":"Born 1605"}
            ]
        }"#;
        let out = resolve_json(req).unwrap();
        let v: serde_json::Value = serde_json::from_str(&out).unwrap();
        // Both profiles land in one cluster; one scored edge justifies it.
        assert_eq!(v["clusters"].as_array().unwrap().len(), 1);
        assert_eq!(v["edges"].as_array().unwrap().len(), 1);
    }

    #[test]
    fn resolve_json_rejects_mismatched_embeddings() {
        let req = r#"{
            "profiles":[{"kind":"node","name":"A","category":"C","anchor":"UNKNOWN","attribute":"UNKNOWN"}],
            "embeddings":[[1.0],[2.0]]
        }"#;
        assert!(resolve_json(req).unwrap_err().contains("embeddings length"));
    }

    #[test]
    fn resolve_json_partial_config_override() {
        // Only one config field supplied; the rest take defaults (serde default).
        let req = r#"{
            "profiles":[{"kind":"node","name":"A","category":"C","anchor":"UNKNOWN","attribute":"UNKNOWN"}],
            "config":{"scoring":{"merge_threshold":0.99}}
        }"#;
        assert!(resolve_json(req).is_ok());
    }
}
