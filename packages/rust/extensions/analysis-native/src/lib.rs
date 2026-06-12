//! `goldenanalysis._native` -- native acceleration kernels (PyO3 extension module).
//!
//! Thin Arrow-reading shims over the pyo3-free `analysis-core` crate. Each
//! function is a behaviour-exact replacement for a CPU-bound pure-Python loop in
//! `goldenanalysis/core/aggregate.py`; the Python side
//! (`goldenanalysis/core/_native_loader.py`) selects the native path only when
//! `GOLDENANALYSIS_NATIVE` opts in AND the primitive has cleared parity
//! (`_GATED_ON`, empty until a wall-verified flip), and the pure-Python
//! implementation stays the default + fallback.
//!
//! Data crosses the boundary as a Float64 Arrow array via the C Data Interface
//! (`PyArrowType<ArrayData>`), zero-copy, mirroring goldencheck-native. The shims
//! never touch business logic -- they decode Arrow into a plain `&[f64]` (dropping
//! null slots, whose backing value is undefined) and delegate to `analysis-core`.
use arrow::array::{Array, ArrayData, Float64Array};
use arrow::pyarrow::PyArrowType;
use pyo3::prelude::*;

/// Read a Float64 Arrow array into a `Vec<f64>`, dropping null slots. Raises
/// `TypeError` on non-Float64 input (cast in Polars before `.to_arrow()`).
fn read_f64(values: PyArrowType<ArrayData>, fn_name: &str) -> PyResult<Vec<f64>> {
    let data = values.0;
    if !matches!(data.data_type(), arrow::datatypes::DataType::Float64) {
        return Err(pyo3::exceptions::PyTypeError::new_err(format!(
            "{fn_name} expects a Float64 array, got {:?}",
            data.data_type()
        )));
    }
    let arr = Float64Array::from(data);
    Ok(if arr.null_count() == 0 {
        arr.values().to_vec()
    } else {
        (0..arr.len())
            .filter(|&i| !arr.is_null(i))
            .map(|i| arr.value(i))
            .collect()
    })
}

/// Equal-width histogram over a Float64 Arrow column -- the native mirror of
/// `goldenanalysis.core.aggregate.histogram`. Returns `[(left_edge, count), ...]`.
#[pyfunction]
fn histogram(values: PyArrowType<ArrayData>, bins: i64) -> PyResult<Vec<(f64, i64)>> {
    let vals = read_f64(values, "histogram")?;
    Ok(analysis_core::histogram(&vals, bins))
}

/// Linear-interpolation quantile of a Float64 Arrow column -- the native mirror of
/// `goldenanalysis.core.aggregate.quantile`.
#[pyfunction]
fn quantile(values: PyArrowType<ArrayData>, q: f64) -> PyResult<f64> {
    let vals = read_f64(values, "quantile")?;
    Ok(analysis_core::quantile(&vals, q))
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(histogram, m)?)?;
    m.add_function(wrap_pyfunction!(quantile, m)?)?;
    Ok(())
}
