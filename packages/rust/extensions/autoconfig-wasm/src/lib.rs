//! wasm-bindgen wrapper over `goldenmatch-autoconfig-core` (JSON in / JSON out),
//! mirroring the PyO3 shims in the `native` crate so Python and JS/TS share ONE
//! decision core. The structured contract lives in the core crate; each surface
//! serializes its input to JSON, this deserializes -> calls the core -> serializes
//! back. Parity is structural (one crate), not asserted after the fact.
use goldenmatch_autoconfig_core::{
    assemble_strong_id_union, classify_by_name, classify_columns, decide_plan,
    exact_matchkey_floor, extrapolate_pair_count, finalize_strong_id_union, sparse_match_floor,
    BlockingColumnInput, ColumnStats, ExtrapolationInput, PlannerInput, UnionFinalizeInput,
};
use wasm_bindgen::prelude::*;

/// Layer 1 planner: a JSON `PlannerInput` -> a JSON `ExecutionPlan`.
#[wasm_bindgen]
pub fn autoconfig_decide_plan(input_json: &str) -> Result<String, JsError> {
    let input: PlannerInput = serde_json::from_str(input_json)
        .map_err(|e| JsError::new(&format!("bad PlannerInput json: {e}")))?;
    let plan = decide_plan(&input);
    serde_json::to_string(&plan).map_err(|e| JsError::new(&e.to_string()))
}

/// Layer 2 classifier: a JSON array of `ColumnStats` -> a JSON array of `ColumnProfile`.
#[wasm_bindgen]
pub fn autoconfig_classify_columns(cols_json: &str) -> Result<String, JsError> {
    let cols: Vec<ColumnStats> = serde_json::from_str(cols_json)
        .map_err(|e| JsError::new(&format!("bad ColumnStats json: {e}")))?;
    let out = classify_columns(&cols);
    serde_json::to_string(&out).map_err(|e| JsError::new(&e.to_string()))
}

/// S1 extrapolation: a JSON `ExtrapolationInput` -> a JSON `ExtrapolationOutput`.
#[wasm_bindgen]
pub fn autoconfig_extrapolate_pair_count(input_json: &str) -> Result<String, JsError> {
    let input: ExtrapolationInput = serde_json::from_str(input_json)
        .map_err(|e| JsError::new(&format!("bad ExtrapolationInput json: {e}")))?;
    let out = extrapolate_pair_count(&input);
    serde_json::to_string(&out).map_err(|e| JsError::new(&e.to_string()))
}

/// S2b: JSON `{"estimated_pairs": N}` -> JSON `{"floor": M}`.
#[wasm_bindgen]
pub fn autoconfig_sparse_match_floor(input_json: &str) -> Result<String, JsError> {
    let v: serde_json::Value = serde_json::from_str(input_json)
        .map_err(|e| JsError::new(&format!("bad sparse_match_floor json: {e}")))?;
    let estimated_pairs = v
        .get("estimated_pairs")
        .and_then(|x| x.as_u64())
        .ok_or_else(|| JsError::new("missing/invalid estimated_pairs"))?;
    let floor = sparse_match_floor(estimated_pairs);
    Ok(serde_json::json!({ "floor": floor }).to_string())
}

/// S3: JSON `{"col_type": "email"}` -> JSON `{"floor": 0.7}`.
#[wasm_bindgen]
pub fn autoconfig_exact_matchkey_floor(input_json: &str) -> Result<String, JsError> {
    let v: serde_json::Value = serde_json::from_str(input_json)
        .map_err(|e| JsError::new(&format!("bad exact_matchkey_floor json: {e}")))?;
    let col_type = v
        .get("col_type")
        .and_then(|x| x.as_str())
        .ok_or_else(|| JsError::new("missing/invalid col_type"))?;
    let floor = exact_matchkey_floor(col_type);
    Ok(serde_json::json!({ "floor": floor }).to_string())
}

/// Name-pattern classifier: a JSON `{"name": "first_name"}` -> a JSON col_type
/// string (`"name"`, `"date"`, …) or `null`. This is the name-*pattern*-only
/// classifier the strong-id union uses for name-column detection (Python
/// `_classify_by_name`), distinct from the data-aware `classify_columns` — it is
/// the name-classification authority the pure-TS union port pins itself against.
#[wasm_bindgen]
pub fn autoconfig_classify_by_name(input_json: &str) -> Result<String, JsError> {
    let v: serde_json::Value = serde_json::from_str(input_json)
        .map_err(|e| JsError::new(&format!("bad classify_by_name json: {e}")))?;
    let name = v
        .get("name")
        .and_then(|x| x.as_str())
        .ok_or_else(|| JsError::new("missing/invalid name"))?;
    let out = classify_by_name(name);
    serde_json::to_string(&out).map_err(|e| JsError::new(&e.to_string()))
}

/// Blocking selection, phase 1: a JSON array of `BlockingColumnInput` -> a JSON
/// array of `UnionPass` (the #1207 strong-identifier union candidates) or `null`.
#[wasm_bindgen]
pub fn autoconfig_assemble_strong_id_union(cols_json: &str) -> Result<String, JsError> {
    let cols: Vec<BlockingColumnInput> = serde_json::from_str(cols_json)
        .map_err(|e| JsError::new(&format!("bad BlockingColumnInput json: {e}")))?;
    let out = assemble_strong_id_union(&cols);
    serde_json::to_string(&out).map_err(|e| JsError::new(&e.to_string()))
}

/// Blocking selection, phase 2: a JSON `UnionFinalizeInput` -> a JSON
/// `BlockingConfigOut` (the emitted `multi_pass` union) or `null`.
#[wasm_bindgen]
pub fn autoconfig_finalize_strong_id_union(input_json: &str) -> Result<String, JsError> {
    let input: UnionFinalizeInput = serde_json::from_str(input_json)
        .map_err(|e| JsError::new(&format!("bad UnionFinalizeInput json: {e}")))?;
    let out = finalize_strong_id_union(&input);
    serde_json::to_string(&out).map_err(|e| JsError::new(&e.to_string()))
}
