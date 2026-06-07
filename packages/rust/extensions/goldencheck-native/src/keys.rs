//! Arrow-reading shims for the combinatorial key / functional-dependency
//! kernels in `goldencheck-core`.
//!
//! Each column is interned to dense `u64` value-ids (nulls get their own id) so
//! the core kernels stay dtype-agnostic and exact (equality is on the real
//! value, never a lossy hash). Supports the dtypes the deep-profiler hands us:
//! Utf8/LargeUtf8, the signed/unsigned ints, Float64/Float32, and Boolean.
use arrow::array::{
    Array, ArrayData, BooleanArray, Float32Array, Float64Array, Int16Array, Int32Array, Int64Array,
    Int8Array, LargeStringArray, StringArray, UInt16Array, UInt32Array, UInt64Array, UInt8Array,
};
use arrow::datatypes::DataType;
use arrow::pyarrow::PyArrowType;
use pyo3::prelude::*;
use rustc_hash::FxHashMap;

/// Intern one Arrow column to dense `u64` value-ids. Null slots all share a
/// single reserved id distinct from every real value's id.
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
        // Floats keyed by bit pattern (exact value identity; matches Polars
        // equality semantics for finite values, which is all a key column has).
        DataType::Float32 => {
            intern_primitive!(Float32Array::from(data), u32, |a: &Float32Array, i| a
                .value(i)
                .to_bits())
        }
        DataType::Float64 => {
            intern_primitive!(Float64Array::from(data), u64, |a: &Float64Array, i| a
                .value(i)
                .to_bits())
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
            "key/FD kernels do not support Arrow dtype {other:?}; \
             cast to a supported type (string/int/float/bool) first"
        ))),
    }
}

/// Search for minimal composite keys over `field_arrays`.
///
/// `single_unique[c]` marks columns already unique on their own (the caller
/// detects these cheaply and reports them as simple keys); subsets touching
/// them are skipped so results are genuinely composite. Returns each key as a
/// sorted list of column indices into `field_arrays`. Delegates to
/// `goldencheck_core::composite_key_search`.
#[pyfunction]
#[pyo3(signature = (field_arrays, max_size, single_unique))]
pub fn composite_key_search(
    field_arrays: Vec<PyArrowType<ArrayData>>,
    max_size: usize,
    single_unique: Vec<bool>,
) -> PyResult<Vec<Vec<usize>>> {
    if field_arrays.is_empty() {
        return Ok(Vec::new());
    }
    let columns: Vec<Vec<u64>> = field_arrays
        .into_iter()
        .map(|a| intern_column(a.0))
        .collect::<PyResult<_>>()?;
    let n_rows = columns[0].len();
    let refs: Vec<&[u64]> = columns.iter().map(|c| c.as_slice()).collect();
    Ok(goldencheck_core::composite_key_search(
        &refs,
        n_rows,
        max_size,
        &single_unique,
    ))
}

/// Whether `lhs -> rhs` holds (every distinct lhs value maps to one rhs value).
/// Delegates to `goldencheck_core::functional_dependency_holds`.
#[pyfunction]
pub fn functional_dependency_holds(
    lhs: PyArrowType<ArrayData>,
    rhs: PyArrowType<ArrayData>,
) -> PyResult<bool> {
    let l = intern_column(lhs.0)?;
    let r = intern_column(rhs.0)?;
    if l.len() != r.len() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "functional_dependency_holds: lhs and rhs differ in length",
        ));
    }
    Ok(goldencheck_core::functional_dependency_holds(&l, &r))
}

/// Discover all strict single-column FDs `(det_idx, dep_idx)` among
/// `field_arrays`. Interns each column once and reuses it across every pair
/// (delegates to `goldencheck_core::discover_functional_dependencies`).
#[pyfunction]
pub fn discover_functional_dependencies(
    field_arrays: Vec<PyArrowType<ArrayData>>,
) -> PyResult<Vec<(usize, usize)>> {
    if field_arrays.is_empty() {
        return Ok(Vec::new());
    }
    let columns: Vec<Vec<u64>> = field_arrays
        .into_iter()
        .map(|a| intern_column(a.0))
        .collect::<PyResult<_>>()?;
    let refs: Vec<&[u64]> = columns.iter().map(|c| c.as_slice()).collect();
    Ok(goldencheck_core::discover_functional_dependencies(&refs))
}
