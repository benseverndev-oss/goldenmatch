//! wasm-bindgen wrapper over `goldenmatch-documents-core` (string in / string
//! out), mirroring the PyO3 shims in `native/src/documents.rs` so Python and
//! JS/TS share ONE document-ingest kernel implementation. No logic lives here.
use goldenmatch_documents_core as core;
use wasm_bindgen::prelude::*;

#[wasm_bindgen]
pub fn schema_validate(schema_json: &str) -> Result<String, JsError> {
    core::schema::schema_from_json(schema_json)
        .map(|s| core::schema::schema_to_json(&s))
        .map_err(|e| JsError::new(&e))
}

#[wasm_bindgen]
pub fn parse_message_text(resp_json: &str) -> Result<String, JsError> {
    core::parse::parse_message_text(resp_json).map_err(|e| JsError::new(&e))
}

#[wasm_bindgen]
pub fn extract_instruction(schema_json: &str) -> Result<String, JsError> {
    let s = core::schema::schema_from_json(schema_json).map_err(|e| JsError::new(&e))?;
    Ok(core::prompt::extract_instruction(&s))
}

#[wasm_bindgen]
pub fn suggest_prompt() -> String {
    core::prompt::suggest_prompt().to_string()
}

#[wasm_bindgen]
pub fn normalize_record(
    values_json: &str,
    confidence_json: &str,
    schema_json: &str,
) -> Result<String, JsError> {
    let s = core::schema::schema_from_json(schema_json).map_err(|e| JsError::new(&e))?;
    let row = core::normalize::normalize_record(values_json, confidence_json, &s)
        .map_err(|e| JsError::new(&e))?;
    let values: serde_json::Map<_, _> =
        row.values.into_iter().map(|(k, v)| (k, serde_json::json!(v))).collect();
    let confidence: serde_json::Map<_, _> =
        row.confidence.into_iter().map(|(k, v)| (k, serde_json::json!(v))).collect();
    Ok(serde_json::json!({"values": values, "confidence": confidence}).to_string())
}
