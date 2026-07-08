//! Arrow-reading shims for the column-profiling kernels.
use arrow::array::{make_array, ArrayData};
use arrow::pyarrow::PyArrowType;
use pyo3::prelude::*;

/// Benford leading-digit histogram for a Float64 Arrow column. Decodes the
/// pyarrow array and delegates to `goldencheck_core::benford_leading_digits`
/// (which owns the Float64 downcast + null handling).
#[pyfunction]
pub fn benford_leading_digits(values: PyArrowType<ArrayData>) -> PyResult<[u64; 9]> {
    let array = make_array(values.0);
    goldencheck_core::benford_leading_digits(array.as_ref())
        .map_err(|e| pyo3::exceptions::PyTypeError::new_err(e.to_string()))
}
