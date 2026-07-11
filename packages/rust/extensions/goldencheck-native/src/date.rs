//! PyO3 shim over `goldencheck_core::date`. Input: Python `list[str | None]` +
//! a format string -> `list[str | None]` (canonical ISO or None).
use pyo3::prelude::*;

#[pyfunction]
pub fn str_to_date(values: Vec<Option<String>>, fmt: &str) -> Vec<Option<String>> {
    goldencheck_core::str_to_date(&values, fmt)
}
