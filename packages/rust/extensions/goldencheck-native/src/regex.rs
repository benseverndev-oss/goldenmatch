//! PyO3 shims over `goldencheck_core::regex`. Input arrives as a Python
//! `list[str | None]` (auto -> `Vec<Option<String>>`); a bad pattern -> ValueError.
//! Do NOT `use goldencheck_core::str_contains_count` etc. -- the shim fns share
//! those names; bodies call them fully-qualified to avoid an E0255 clash.
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

#[pyfunction]
pub fn str_contains_count(values: Vec<Option<String>>, pattern: &str) -> PyResult<usize> {
    goldencheck_core::str_contains_count(&values, pattern).map_err(|e| PyValueError::new_err(e.to_string()))
}

#[pyfunction]
pub fn str_filter_mask(values: Vec<Option<String>>, pattern: &str) -> PyResult<Vec<Option<bool>>> {
    goldencheck_core::str_filter_mask(&values, pattern).map_err(|e| PyValueError::new_err(e.to_string()))
}

#[pyfunction]
pub fn str_replace_all(values: Vec<Option<String>>, pattern: &str, replacement: &str) -> PyResult<Vec<Option<String>>> {
    goldencheck_core::str_replace_all(&values, pattern, replacement).map_err(|e| PyValueError::new_err(e.to_string()))
}
