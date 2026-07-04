//! wasm-bindgen wrapper over `fingerprint-core`, so the JS/TS record
//! fingerprint runs the SAME canonicalization kernel as the Python native path
//! and the DuckDB / Postgres surfaces — one source of truth for the
//! cross-surface stable record-id hash. Edge-safe (pure wasm, no `node:*`).
//! Mirrors the sibling `graph-wasm` / `sketch-wasm` / `goldenhnsw-wasm` shims.
//!
//! The record crosses the boundary as a JSON object string (the same public
//! entry the SQL surfaces use) and the 64-hex digest crosses back as a string —
//! no typed-array marshaling. `__`-prefixed keys are dropped and values are
//! type-tagged inside the core, so the byte layout (and thus the hash) is
//! identical to every other surface.

use goldenmatch_fingerprint_core::fingerprint_json as core_fingerprint_json;
use wasm_bindgen::prelude::*;

/// Canonical SHA-256 fingerprint (64 lowercase hex) of a record given as a JSON
/// object string. Drops `__`-prefixed keys; values are type-tagged (int `1` !=
/// str `"1"` != bool). Throws (rejects) on invalid JSON, a non-object, a nested
/// array/object value, or a non-finite float — matching the core's contract.
#[wasm_bindgen]
pub fn fingerprint_json(record_json: &str) -> Result<String, JsError> {
    core_fingerprint_json(record_json).map_err(|e| JsError::new(&e))
}
