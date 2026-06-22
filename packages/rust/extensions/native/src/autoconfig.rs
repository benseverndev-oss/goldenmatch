//! Thin PyO3 shims over goldenmatch-autoconfig-core (JSON in / JSON out).
//! The structured contract lives in the core crate; Python serializes the
//! input dict to JSON, this deserializes -> calls the core -> serializes back.
use pyo3::prelude::*;
use pyo3::exceptions::PyValueError;
use goldenmatch_autoconfig_core::{
    classify_columns, decide_plan, extrapolate_pair_count, ColumnStats, ExtrapolationInput,
    PlannerInput,
};

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
