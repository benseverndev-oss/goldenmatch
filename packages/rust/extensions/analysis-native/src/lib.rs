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
use analysis_core::{intern_f64, intern_i64, intern_str};
use arrow::array::{
    make_array, Array, ArrayData, BooleanArray, Float32Array, Float64Array, Int16Array, Int32Array,
    Int64Array, Int8Array, LargeStringArray, StringArray, UInt16Array, UInt32Array, UInt64Array,
    UInt8Array,
};
use arrow::datatypes::DataType;
use arrow::pyarrow::PyArrowType;
use pyo3::prelude::*;

/// Intern one Arrow column to dense `u64` value-ids by extracting a typed buffer
/// (+ byte-per-row validity) and delegating to the shared `analysis-core`
/// interner (#1788) -- ONE arrow-free implementation shared with the wasm / SQL
/// surfaces, so they cannot drift. Null slots share id 0; non-null get dense ids
/// from 1; floats canonicalize NaN / signed-zero (`analysis_core::canon_f64_bits`)
/// to match Polars equality. Every integer width, unsigned int, and bool reaches
/// `intern_i64` by value promotion (`as i64` is bijective for all of them -- u64
/// bit-reinterprets, bool -> 0/1), so the equality PARTITION the frame kernels
/// depend on is preserved; the specific ids are never observed by a caller.
fn intern_column(data: ArrayData) -> PyResult<Vec<u64>> {
    let n = data.len();

    // A byte-per-row validity buffer (0 = null) for any concrete Arrow array.
    macro_rules! validity {
        ($arr:expr) => {{
            let a = &$arr;
            (0..n)
                .map(|i| if a.is_null(i) { 0u8 } else { 1u8 })
                .collect::<Vec<u8>>()
        }};
    }
    // Promote any int/uint/bool array to i64 + validity, then core-intern. `as i64`
    // is bijective for every one of these (u64 bit-reinterprets, bool -> 0/1), so
    // the equality partition is identical; the raw ids are unobservable.
    macro_rules! intern_as_i64 {
        ($arrty:ty) => {{
            let arr = <$arrty>::from(data);
            let valid = validity!(arr);
            let vals: Vec<i64> = (0..n).map(|i| arr.value(i) as i64).collect();
            Ok(intern_i64(&vals, &valid))
        }};
    }
    macro_rules! intern_as_f64 {
        ($arrty:ty) => {{
            let arr = <$arrty>::from(data);
            let valid = validity!(arr);
            let vals: Vec<f64> = (0..n).map(|i| arr.value(i) as f64).collect();
            Ok(intern_f64(&vals, &valid))
        }};
    }
    macro_rules! intern_as_str {
        ($arrty:ty) => {{
            let arr = <$arrty>::from(data);
            let valid = validity!(arr);
            // Rebuild a contiguous utf8 offsets/bytes buffer; null rows get an empty
            // span (validity marks them, so `intern_str` skips them regardless).
            let mut bytes: Vec<u8> = Vec::new();
            let mut offsets: Vec<u32> = Vec::with_capacity(n + 1);
            offsets.push(0);
            for i in 0..n {
                if valid[i] != 0 {
                    bytes.extend_from_slice(arr.value(i).as_bytes());
                }
                offsets.push(bytes.len() as u32);
            }
            Ok(intern_str(&offsets, &bytes, &valid))
        }};
    }

    match data.data_type() {
        DataType::Utf8 => intern_as_str!(StringArray),
        DataType::LargeUtf8 => intern_as_str!(LargeStringArray),
        DataType::Int8 => intern_as_i64!(Int8Array),
        DataType::Int16 => intern_as_i64!(Int16Array),
        DataType::Int32 => intern_as_i64!(Int32Array),
        DataType::Int64 => intern_as_i64!(Int64Array),
        DataType::UInt8 => intern_as_i64!(UInt8Array),
        DataType::UInt16 => intern_as_i64!(UInt16Array),
        DataType::UInt32 => intern_as_i64!(UInt32Array),
        DataType::UInt64 => intern_as_i64!(UInt64Array),
        DataType::Float32 => intern_as_f64!(Float32Array),
        DataType::Float64 => intern_as_f64!(Float64Array),
        DataType::Boolean => intern_as_i64!(BooleanArray),
        other => Err(pyo3::exceptions::PyTypeError::new_err(format!(
            "frame kernels do not support Arrow dtype {other:?}; \
             cast to a supported type (string/int/float/bool) first"
        ))),
    }
}

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

/// Fraction of rows that are exact duplicates across the given key columns --
/// the native mirror of the duplicate-row check in
/// `goldenanalysis.core.aggregate`. Each column is interned to dense `u64`
/// value-ids first (dtype-agnostic, exact) and delegated to `analysis-core`.
#[pyfunction]
fn duplicate_row_ratio(cols: Vec<PyArrowType<ArrayData>>) -> PyResult<f64> {
    let interned: Vec<Vec<u64>> = cols
        .into_iter()
        .map(|c| intern_column(c.0))
        .collect::<PyResult<Vec<_>>>()?;
    let n = interned.first().map(|c| c.len()).unwrap_or(0);
    Ok(analysis_core::duplicate_row_ratio(&interned, n))
}

/// Number of distinct values (nulls collapse to a single distinct id) in a
/// single Arrow column -- native mirror of the distinct-count aggregate.
#[pyfunction]
fn distinct_count(col: PyArrowType<ArrayData>) -> PyResult<i64> {
    let interned = intern_column(col.0)?;
    Ok(analysis_core::distinct_count(&interned))
}

/// Arithmetic mean of a Float64 Arrow column -- native mirror of
/// `goldenanalysis.core.aggregate.mean`.
#[pyfunction]
fn mean(values: PyArrowType<ArrayData>) -> PyResult<f64> {
    let vals = read_f64(values, "mean")?;
    Ok(analysis_core::mean(&vals))
}

/// Minimum of a Float64 Arrow column -- native mirror of
/// `goldenanalysis.core.aggregate.min`.
#[pyfunction]
fn min(values: PyArrowType<ArrayData>) -> PyResult<f64> {
    let vals = read_f64(values, "min")?;
    Ok(analysis_core::min(&vals))
}

/// Maximum of a Float64 Arrow column -- native mirror of
/// `goldenanalysis.core.aggregate.max`.
#[pyfunction]
fn max(values: PyArrowType<ArrayData>) -> PyResult<f64> {
    let vals = read_f64(values, "max")?;
    Ok(analysis_core::max(&vals))
}

/// Discrete cluster-size histogram of a Float64 Arrow column -- native mirror of
/// `goldenanalysis.core.aggregate.cluster_size_histogram`.
#[pyfunction]
fn cluster_size_histogram(values: PyArrowType<ArrayData>) -> PyResult<Vec<i64>> {
    let vals = read_f64(values, "cluster_size_histogram")?;
    Ok(analysis_core::cluster_size_histogram(&vals))
}

/// Per-column null ratio (`null_count / len`, 0.0 for empty columns) for each
/// Arrow column. Reads Arrow null buffers directly -- no interning needed.
///
/// Uses `logical_null_count`, NOT `null_count`: an all-null column has the Arrow
/// `Null` dtype (a `NullArray`), which carries no physical validity buffer, so
/// `null_count()` reports 0 and the ratio would wrongly come out 0.0 instead of
/// 1.0. `logical_null_count()` counts a `NullArray` as all-null and is identical
/// to `null_count()` for every supported dtype (a validity-buffer-backed array).
#[pyfunction]
fn null_ratio_per_column(cols: Vec<PyArrowType<ArrayData>>) -> PyResult<Vec<f64>> {
    Ok(cols
        .into_iter()
        .map(|c| {
            let arr = make_array(c.0);
            let n = arr.len();
            if n == 0 {
                0.0
            } else {
                arr.logical_null_count() as f64 / n as f64
            }
        })
        .collect())
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(histogram, m)?)?;
    m.add_function(wrap_pyfunction!(quantile, m)?)?;
    m.add_function(wrap_pyfunction!(duplicate_row_ratio, m)?)?;
    m.add_function(wrap_pyfunction!(distinct_count, m)?)?;
    m.add_function(wrap_pyfunction!(null_ratio_per_column, m)?)?;
    m.add_function(wrap_pyfunction!(mean, m)?)?;
    m.add_function(wrap_pyfunction!(min, m)?)?;
    m.add_function(wrap_pyfunction!(max, m)?)?;
    m.add_function(wrap_pyfunction!(cluster_size_histogram, m)?)?;
    Ok(())
}
