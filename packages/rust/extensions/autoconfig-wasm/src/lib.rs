//! wasm-bindgen wrapper over `goldenmatch-autoconfig-core` (JSON in / JSON out),
//! mirroring the PyO3 shims in the `native` crate so Python and JS/TS share ONE
//! decision core. The structured contract lives in the core crate; each surface
//! serializes its input to JSON, this deserializes -> calls the core -> serializes
//! back. Parity is structural (one crate), not asserted after the fact.
use goldenmatch_autoconfig_core::{classify_columns, decide_plan, ColumnStats, PlannerInput};
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
