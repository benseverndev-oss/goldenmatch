//! Arrow-reading shims for the column-profiling kernels.
use arrow::array::{make_array, ArrayData};
use arrow::pyarrow::PyArrowType;
use pyo3::prelude::*;

/// Benford leading-digit histogram for a Float64 Arrow column. Decodes the
/// pyarrow array and delegates to `goldencheck_core::benford_leading_digits`
/// (which owns the Float64 downcast + null handling). Returns the 9 per-digit
/// counts (digits 1..=9) as a Python list -- the same `Counter` the
/// pure-Python `goldencheck.baseline.statistical._compute_benford` builds, so
/// the caller's chi-squared step is unchanged.
///
/// The caller must pass a Float64 array (cast in Polars before `.to_arrow()`);
/// non-Float64 input raises `TypeError`.
#[pyfunction]
pub fn benford_leading_digits(values: PyArrowType<ArrayData>) -> PyResult<[u64; 9]> {
    let array = make_array(values.0);
    goldencheck_core::benford_leading_digits(array.as_ref())
        .map_err(|e| pyo3::exceptions::PyTypeError::new_err(e.to_string()))
}
