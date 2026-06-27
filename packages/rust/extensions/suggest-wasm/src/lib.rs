//! wasm-bindgen wrapper over `goldenmatch-suggest-core` (JSON in / JSON out),
//! mirroring the PyO3 shims in the `native` crate so Python and JS/TS share ONE
//! suggestion kernel. The structured contract lives in the core crate; each
//! surface packs its inputs to JSON, this deserializes -> calls the core ->
//! serializes back. Parity is structural (one crate), not asserted after the fact.
use goldenmatch_suggest_core::suggest_from_json;
use wasm_bindgen::prelude::*;

/// The healer entry: the five `suggest_from_json` args packed into one JSON
/// object (all String fields) -> a JSON array of `Suggestion` objects.
#[wasm_bindgen]
pub fn suggest_review(input_json: &str) -> Result<String, JsError> {
    #[derive(serde::Deserialize)]
    struct In {
        scored_pairs: String,
        clusters: String,
        column_signals: String,
        config: String,
        priors: String,
    }
    let i: In = serde_json::from_str(input_json)
        .map_err(|e| JsError::new(&format!("bad suggest input json: {e}")))?;
    suggest_from_json(
        &i.scored_pairs,
        &i.clusters,
        &i.column_signals,
        &i.config,
        &i.priors,
    )
    .map_err(|e| JsError::new(&e))
}
