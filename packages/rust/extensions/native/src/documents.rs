//! Thin PyO3 shims exposing `goldenmatch_documents_core` to Python (string in /
//! string out, mirrors `suggest.rs` and `autoconfig.rs`). No logic lives here.
use goldenmatch_documents_core as core;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

#[pyfunction]
pub fn documents_schema_validate(schema_json: &str) -> PyResult<String> {
    core::schema::schema_from_json(schema_json)
        .map(|s| core::schema::schema_to_json(&s))
        .map_err(PyValueError::new_err)
}
#[pyfunction]
pub fn documents_parse_message_text(resp_json: &str) -> PyResult<String> {
    core::parse::parse_message_text(resp_json).map_err(PyValueError::new_err)
}
#[pyfunction]
pub fn documents_extract_instruction(schema_json: &str) -> PyResult<String> {
    let s = core::schema::schema_from_json(schema_json).map_err(PyValueError::new_err)?;
    Ok(core::prompt::extract_instruction(&s))
}
#[pyfunction]
pub fn documents_suggest_prompt() -> String {
    core::prompt::suggest_prompt().to_string()
}
#[pyfunction]
pub fn documents_template(doctype: &str) -> PyResult<String> {
    core::templates::template_json(doctype).map_err(PyValueError::new_err)
}
#[pyfunction]
pub fn documents_template_list() -> String {
    core::templates::template_list_json()
}
#[pyfunction]
pub fn documents_classify_prompt() -> String {
    core::classify::classify_prompt().to_string()
}
#[pyfunction]
pub fn documents_parse_classify(text: &str) -> PyResult<String> {
    core::classify::parse_classify_json(text).map_err(PyValueError::new_err)
}
#[pyfunction]
pub fn documents_parse_structured(text: &str, template_json: &str) -> PyResult<String> {
    let t = core::templates::template_from_json(template_json).map_err(PyValueError::new_err)?;
    core::extract_structured::parse_structured_json(text, &t).map_err(PyValueError::new_err)
}
#[pyfunction]
pub fn documents_normalize_record(
    values_json: &str,
    confidence_json: &str,
    schema_json: &str,
) -> PyResult<String> {
    let s = core::schema::schema_from_json(schema_json).map_err(PyValueError::new_err)?;
    let row = core::normalize::normalize_record(values_json, confidence_json, &s)
        .map_err(PyValueError::new_err)?;
    // return {"values": {...}, "confidence": {...}} as JSON for the Python side
    let values: serde_json::Map<_, _> = row
        .values
        .into_iter()
        .map(|(k, v)| (k, serde_json::json!(v)))
        .collect();
    let confidence: serde_json::Map<_, _> = row
        .confidence
        .into_iter()
        .map(|(k, v)| (k, serde_json::json!(v)))
        .collect();
    Ok(serde_json::json!({"values": values, "confidence": confidence}).to_string())
}
