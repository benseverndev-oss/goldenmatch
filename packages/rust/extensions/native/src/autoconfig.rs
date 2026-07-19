//! Thin PyO3 shims over goldenmatch-autoconfig-core (JSON in / JSON out).
//! The structured contract lives in the core crate; Python serializes the
//! input dict to JSON, this deserializes -> calls the core -> serializes back.
use goldenmatch_autoconfig_core::{
    assemble_strong_id_union, classify_columns, decide_plan, exact_matchkey_floor,
    extrapolate_pair_count, finalize_strong_id_union, sparse_match_floor, BlockingColumnInput,
    ColumnStats, ExtrapolationInput, PlannerInput, UnionFinalizeInput,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

#[pyfunction]
pub fn autoconfig_decide_plan(input_json: &str) -> PyResult<String> {
    let input: PlannerInput = serde_json::from_str(input_json)
        .map_err(|e| PyValueError::new_err(format!("bad PlannerInput json: {e}")))?;
    let plan = decide_plan(&input);
    serde_json::to_string(&plan).map_err(|e| PyValueError::new_err(e.to_string()))
}

#[pyfunction]
pub fn autoconfig_classify_columns(cols_json: &str) -> PyResult<String> {
    let cols: Vec<ColumnStats> = serde_json::from_str(cols_json)
        .map_err(|e| PyValueError::new_err(format!("bad ColumnStats json: {e}")))?;
    let out = classify_columns(&cols);
    serde_json::to_string(&out).map_err(|e| PyValueError::new_err(e.to_string()))
}

#[pyfunction]
pub fn autoconfig_extrapolate_pair_count(input_json: &str) -> PyResult<String> {
    let input: ExtrapolationInput = serde_json::from_str(input_json)
        .map_err(|e| PyValueError::new_err(format!("bad ExtrapolationInput json: {e}")))?;
    let out = extrapolate_pair_count(&input);
    serde_json::to_string(&out).map_err(|e| PyValueError::new_err(e.to_string()))
}

/// S2b: JSON `{"estimated_pairs": N}` -> JSON `{"floor": M}`.
#[pyfunction]
pub fn autoconfig_sparse_match_floor(input_json: &str) -> PyResult<String> {
    let v: serde_json::Value = serde_json::from_str(input_json)
        .map_err(|e| PyValueError::new_err(format!("bad sparse_match_floor json: {e}")))?;
    let estimated_pairs = v
        .get("estimated_pairs")
        .and_then(|x| x.as_u64())
        .ok_or_else(|| PyValueError::new_err("missing/invalid estimated_pairs"))?;
    let floor = sparse_match_floor(estimated_pairs);
    Ok(serde_json::json!({ "floor": floor }).to_string())
}

/// S3: JSON `{"col_type": "email"}` -> JSON `{"floor": 0.7}`.
#[pyfunction]
pub fn autoconfig_exact_matchkey_floor(input_json: &str) -> PyResult<String> {
    let v: serde_json::Value = serde_json::from_str(input_json)
        .map_err(|e| PyValueError::new_err(format!("bad exact_matchkey_floor json: {e}")))?;
    let col_type = v
        .get("col_type")
        .and_then(|x| x.as_str())
        .ok_or_else(|| PyValueError::new_err("missing/invalid col_type"))?;
    let floor = exact_matchkey_floor(col_type);
    Ok(serde_json::json!({ "floor": floor }).to_string())
}

/// Blocking selection, phase 1 (#1207 strong-identifier union): a JSON array of
/// `BlockingColumnInput` -> a JSON array of `UnionPass` (candidate passes) or `null`.
#[pyfunction]
pub fn autoconfig_assemble_strong_id_union(cols_json: &str) -> PyResult<String> {
    let cols: Vec<BlockingColumnInput> = serde_json::from_str(cols_json)
        .map_err(|e| PyValueError::new_err(format!("bad BlockingColumnInput json: {e}")))?;
    let out = assemble_strong_id_union(&cols);
    serde_json::to_string(&out).map_err(|e| PyValueError::new_err(e.to_string()))
}

/// Blocking selection, phase 2 (#1207 strong-identifier union): a JSON
/// `UnionFinalizeInput` -> a JSON `BlockingConfigOut` (the emitted `multi_pass`
/// union) or `null`.
#[pyfunction]
pub fn autoconfig_finalize_strong_id_union(input_json: &str) -> PyResult<String> {
    let input: UnionFinalizeInput = serde_json::from_str(input_json)
        .map_err(|e| PyValueError::new_err(format!("bad UnionFinalizeInput json: {e}")))?;
    let out = finalize_strong_id_union(&input);
    serde_json::to_string(&out).map_err(|e| PyValueError::new_err(e.to_string()))
}
