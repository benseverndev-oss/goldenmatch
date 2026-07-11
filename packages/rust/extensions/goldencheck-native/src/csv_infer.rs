//! PyO3 shim over `goldencheck_core::csv_infer`. Unlike the other kernels in
//! this crate, CSV bytes are not Arrow -- the input is raw bytes tokenized by
//! the pure-Rust `csv` crate inside `goldencheck-core`, so this shim just
//! marshals `Vec<u8>` in and a `dict[str, list]` out (mirroring the shape of
//! `goldencheck.engine.csv_infer.read_csv_owned`, the Python reference this
//! MUST agree with byte-for-byte).
use goldencheck_core::TypedColumn;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

fn typed_column_to_py(py: Python<'_>, col: &TypedColumn) -> PyResult<Py<PyList>> {
    let list = match col {
        TypedColumn::Int(values) => PyList::new(py, values.iter().copied())?,
        TypedColumn::Float(values) => PyList::new(py, values.iter().copied())?,
        TypedColumn::Bool(values) => PyList::new(py, values.iter().copied())?,
        TypedColumn::Str(values) => PyList::new(py, values.iter().cloned())?,
    };
    Ok(list.into())
}

/// Read + type-infer raw CSV bytes per the owned CSV inference contract.
/// Delegates tokenizing + inference to `goldencheck_core::read_csv_owned_bytes`
/// (UTF-8 with Latin-1 fallback, first row as header). Returns a Python
/// `dict[str, list]` -- each column's typed values (`int`/`float`/`bool`/`str`,
/// with `None` for empty cells), same shape as
/// `goldencheck.engine.csv_infer.read_csv_owned`.
#[pyfunction]
#[pyo3(signature = (csv_bytes, delimiter=b','))]
pub fn csv_infer_columns(
    py: Python<'_>,
    csv_bytes: Vec<u8>,
    delimiter: u8,
) -> PyResult<Py<PyDict>> {
    let columns = goldencheck_core::read_csv_owned_bytes(&csv_bytes, delimiter);
    let dict = PyDict::new(py);
    for (name, col) in &columns {
        dict.set_item(name, typed_column_to_py(py, col)?)?;
    }
    Ok(dict.into())
}
