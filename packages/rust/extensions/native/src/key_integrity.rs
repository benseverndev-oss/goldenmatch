//! Structural key-integrity certifier — PyO3 surface over the shared pyo3-free
//! kernel in `goldenmatch-key-integrity-core`.
//!
//! `certify_structural_json` takes the same JSON block the wasm / SQL surfaces
//! use (`{n_rows, group_columns, measures}`) and returns the structural result
//! (`{n_key_groups, duplicate_key_groups, max_fan_out, is_unique_at_grain,
//! measure_fan_out}`) as a JSON string. The group-by uniqueness + fan-out
//! reduction + its byte-for-byte parity contract live in the core crate; this is
//! a thin delegator so Python can reach the SAME kernel as TS/WASM (an opt-in
//! single-source path — the pyarrow group_by stays the default + reference).

use goldenmatch_key_integrity_core::certify_structural_json as core_certify;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// Structural key-integrity certification over a JSON block. Delegates to the
/// shared `key-integrity-core` kernel; raises `ValueError` on invalid JSON / a
/// wrong-shaped input (matching the core's contract).
#[pyfunction]
pub fn certify_structural_json(input_json: &str) -> PyResult<String> {
    core_certify(input_json).map_err(PyValueError::new_err)
}
