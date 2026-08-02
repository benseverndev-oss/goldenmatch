//! wasm-bindgen wrapper over `key-integrity-core`, so the JS/TS structural
//! key-integrity certifier runs the SAME group-by uniqueness + fan-out kernel as
//! the Python reference — one source of truth, no hand-maintained parallel JS
//! reduction. Edge-safe (pure wasm, no `node:*`). Mirrors the sibling
//! fingerprint-wasm / cluster-wasm shims.
//!
//! The block crosses the boundary as a JSON object string
//! (`{n_rows, group_columns, measures}`) and the structural result crosses back
//! as a JSON string (`{n_key_groups, duplicate_key_groups, max_fan_out,
//! is_unique_at_grain, measure_fan_out}`) — no typed-array marshaling.

use goldenmatch_key_integrity_core::certify_structural_json as core_certify;
use wasm_bindgen::prelude::*;

/// Structural key-integrity certification over a JSON block. Throws (rejects) on
/// invalid JSON or a wrong-shaped input, matching the core's contract.
#[wasm_bindgen]
pub fn certify_structural_json(input_json: &str) -> Result<String, JsError> {
    core_certify(input_json).map_err(|e| JsError::new(&e))
}
