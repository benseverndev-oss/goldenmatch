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
use arrow::array::{
    make_array, Array, ArrayData, BooleanArray, Float32Array, Float64Array, Int16Array, Int32Array,
    Int64Array, Int8Array, LargeStringArray, StringArray, UInt16Array, UInt32Array, UInt64Array,
    UInt8Array,
};
use arrow::datatypes::DataType;
use arrow::pyarrow::PyArrowType;
use pyo3::prelude::*;
use rustc_hash::FxHashMap;

/// Canonicalize an `f64` to a single `u64` bit-key matching Polars equality:
/// all NaNs fold to one key, `-0.0` and `+0.0` fold together, everything else
/// keys on its raw bit pattern. Keeps float interning behaviour-exact vs the
/// pure-Python (Polars-backed) path.
#[inline]
fn canon_f64_bits(x: f64) -> u64 {
    if x.is_nan() {
        0x7ff8_0000_0000_0000 // one canonical NaN
    } else if x == 0.0 {
        0.0f64.to_bits() // -0.0 and +0.0 fold (x == 0.0 catches both)
    } else {
        x.to_bits()
    }
}

/// Intern one Arrow column to dense `u64` value-ids. Null slots all share a
/// single reserved id (0) distinct from every real value's id. Float columns
/// canonicalize NaN / signed-zero to match Polars equality (see
/// `canon_f64_bits`). Adapted from `goldencheck-native/src/keys.rs`.
fn intern_column(data: ArrayData) -> PyResult<Vec<u64>> {
    macro_rules! intern_primitive {
        ($arr:expr, $keyty:ty, $keyexpr:expr) => {{
            let arr = $arr;
            let mut map: FxHashMap<$keyty, u64> = FxHashMap::default();
            let mut ids = Vec::with_capacity(arr.len());
            let mut next: u64 = 1; // 0 is reserved for null
            for i in 0..arr.len() {
                if arr.is_null(i) {
                    ids.push(0);
                    continue;
                }
                let key: $keyty = $keyexpr(&arr, i);
                let id = *map.entry(key).or_insert_with(|| {
                    let v = next;
                    next += 1;
                    v
                });
                ids.push(id);
            }
            Ok(ids)
        }};
    }

    match data.data_type() {
        DataType::Utf8 => {
            let arr = StringArray::from(data);
            let mut map: FxHashMap<&str, u64> = FxHashMap::default();
            let mut ids = Vec::with_capacity(arr.len());
            let mut next: u64 = 1;
            for i in 0..arr.len() {
                if arr.is_null(i) {
                    ids.push(0);
                    continue;
                }
                let id = *map.entry(arr.value(i)).or_insert_with(|| {
                    let v = next;
                    next += 1;
                    v
                });
                ids.push(id);
            }
            Ok(ids)
        }
        DataType::LargeUtf8 => {
            let arr = LargeStringArray::from(data);
            let mut map: FxHashMap<&str, u64> = FxHashMap::default();
            let mut ids = Vec::with_capacity(arr.len());
            let mut next: u64 = 1;
            for i in 0..arr.len() {
                if arr.is_null(i) {
                    ids.push(0);
                    continue;
                }
                let id = *map.entry(arr.value(i)).or_insert_with(|| {
                    let v = next;
                    next += 1;
                    v
                });
                ids.push(id);
            }
            Ok(ids)
        }
        DataType::Int8 => {
            intern_primitive!(Int8Array::from(data), i8, |a: &Int8Array, i| a.value(i))
        }
        DataType::Int16 => {
            intern_primitive!(Int16Array::from(data), i16, |a: &Int16Array, i| a.value(i))
        }
        DataType::Int32 => {
            intern_primitive!(Int32Array::from(data), i32, |a: &Int32Array, i| a.value(i))
        }
        DataType::Int64 => {
            intern_primitive!(Int64Array::from(data), i64, |a: &Int64Array, i| a.value(i))
        }
        DataType::UInt8 => {
            intern_primitive!(UInt8Array::from(data), u8, |a: &UInt8Array, i| a.value(i))
        }
        DataType::UInt16 => {
            intern_primitive!(UInt16Array::from(data), u16, |a: &UInt16Array, i| a
                .value(i))
        }
        DataType::UInt32 => {
            intern_primitive!(UInt32Array::from(data), u32, |a: &UInt32Array, i| a
                .value(i))
        }
        DataType::UInt64 => {
            intern_primitive!(UInt64Array::from(data), u64, |a: &UInt64Array, i| a
                .value(i))
        }
        // Floats keyed by a canonicalized bit pattern so NaN / signed-zero fold
        // exactly like Polars equality (see `canon_f64_bits`).
        DataType::Float32 => {
            intern_primitive!(Float32Array::from(data), u64, |a: &Float32Array, i| {
                canon_f64_bits(a.value(i) as f64)
            })
        }
        DataType::Float64 => {
            intern_primitive!(Float64Array::from(data), u64, |a: &Float64Array, i| {
                canon_f64_bits(a.value(i))
            })
        }
        DataType::Boolean => {
            let arr = BooleanArray::from(data);
            let mut ids = Vec::with_capacity(arr.len());
            for i in 0..arr.len() {
                if arr.is_null(i) {
                    ids.push(0);
                } else {
                    ids.push(if arr.value(i) { 1 } else { 2 });
                }
            }
            Ok(ids)
        }
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

/// Per-column null ratio (`null_count / len`, 0.0 for empty columns) for each
/// Arrow column. Reads Arrow null buffers directly -- no interning needed.
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
                arr.null_count() as f64 / n as f64
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
    Ok(())
}
