//! `goldenpipe._native` / `goldenpipe_native._native` — the PyO3 binding for the
//! GoldenPipe planner kernel. Pure marshaling shim: `&str` in -> goldenpipe-core
//! json fn -> `String` out. The core owns all logic; the pure-Python planner is a
//! non-authoritative fallback proven to reproduce these bytes (SP2 parity gate).
use pyo3::prelude::*;

#[pyfunction]
fn resolve_json(input: &str) -> String {
    goldenpipe_core::json::resolve_json(input)
}
#[pyfunction]
fn apply_decision_json(input: &str) -> String {
    goldenpipe_core::json::apply_decision_json(input)
}
#[pyfunction]
fn evaluate_builtin_json(input: &str) -> String {
    goldenpipe_core::json::evaluate_builtin_json(input)
}
#[pyfunction]
fn auto_config_json(input: &str) -> String {
    goldenpipe_core::json::auto_config_json(input)
}
#[pyfunction]
fn skip_if_falsy_json(input: &str) -> String {
    goldenpipe_core::json::skip_if_falsy_json(input)
}
#[pyfunction]
fn plan_pipeline_json(input: &str) -> String {
    goldenpipe_core::json::plan_pipeline_json(input)
}
#[pyfunction]
fn apply_scale_hints_json(input: &str) -> String {
    goldenpipe_core::json::apply_scale_hints_json(input)
}
#[pyfunction]
fn band_of_json(input: &str) -> String {
    goldenpipe_core::json::band_of_json(input)
}
#[pyfunction]
fn build_repair_plan_json(input: &str) -> String {
    goldenpipe_core::json::build_repair_plan_json(input)
}
#[pyfunction]
fn lower_json(input: &str) -> String {
    goldenpipe_core::json::lower_json(input)
}
#[pyfunction]
fn provenance_json(input: &str) -> String {
    goldenpipe_core::json::provenance_json(input)
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(resolve_json, m)?)?;
    m.add_function(wrap_pyfunction!(apply_decision_json, m)?)?;
    m.add_function(wrap_pyfunction!(evaluate_builtin_json, m)?)?;
    m.add_function(wrap_pyfunction!(auto_config_json, m)?)?;
    m.add_function(wrap_pyfunction!(skip_if_falsy_json, m)?)?;
    m.add_function(wrap_pyfunction!(plan_pipeline_json, m)?)?;
    m.add_function(wrap_pyfunction!(apply_scale_hints_json, m)?)?;
    m.add_function(wrap_pyfunction!(band_of_json, m)?)?;
    m.add_function(wrap_pyfunction!(build_repair_plan_json, m)?)?;
    m.add_function(wrap_pyfunction!(lower_json, m)?)?;
    m.add_function(wrap_pyfunction!(provenance_json, m)?)?;
    Ok(())
}
