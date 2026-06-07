//! Arrow-reading shims for the column-profiling kernels.
use arrow::array::{Array, ArrayData, Float64Array};
use arrow::pyarrow::PyArrowType;
use pyo3::prelude::*;

/// Benford leading-digit histogram for a Float64 Arrow column.
///
/// Reads the array zero-copy via the Arrow C Data Interface, drops null slots
/// (their backing value is undefined), and delegates to
/// `goldencheck_core::benford_leading_digits`. Returns the 9 per-digit counts
/// (digits 1..=9) as a Python list -- the same `Counter` the pure-Python
/// `goldencheck.baseline.statistical._compute_benford` builds, so the caller's
/// chi-squared step is unchanged.
///
/// The caller must pass a Float64 array (cast in Polars before `.to_arrow()`);
/// non-Float64 input raises `TypeError`.
#[pyfunction]
pub fn benford_leading_digits(values: PyArrowType<ArrayData>) -> PyResult<[u64; 9]> {
    let data = values.0;
    if !matches!(data.data_type(), arrow::datatypes::DataType::Float64) {
        return Err(pyo3::exceptions::PyTypeError::new_err(format!(
            "benford_leading_digits expects a Float64 array, got {:?}",
            data.data_type()
        )));
    }
    let arr = Float64Array::from(data);
    // Honour the null mask: a null slot's backing f64 is undefined.
    let vals: Vec<f64> = if arr.null_count() == 0 {
        arr.values().to_vec()
    } else {
        (0..arr.len())
            .filter(|&i| !arr.is_null(i))
            .map(|i| arr.value(i))
            .collect()
    };
    Ok(goldencheck_core::benford_leading_digits(&vals))
}
