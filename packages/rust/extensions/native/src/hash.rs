//! Canonical record fingerprint — PyO3 + C ABI surfaces over the shared
//! pyo3-free canonicalizer in `goldenmatch-fingerprint-core`.
//!
//! - `record_fingerprint` (PyO3) — maps a Python `dict` to `core::FpValue` and
//!   calls `core::fingerprint_fields`.
//! - `gm_record_fingerprint` (C ABI) — parses a JSON object via
//!   `core::fingerprint_json`, for non-Python callers.
//!
//! The canonicalization spec + the byte-for-byte parity contract with
//! `goldenmatch.core._hashing._fingerprint_py` live in the core crate.
use std::ffi::CStr;
use std::os::raw::{c_char, c_int};

use arrow::array::{
    Array, ArrayData, BooleanArray, Float64Array, Int64Array, LargeStringArray, StringArray,
};
use arrow::datatypes::DataType;
use arrow::pyarrow::PyArrowType;
use goldenmatch_fingerprint_core::{fingerprint_fields, fingerprint_json, FpValue};
use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyString};
use rayon::prelude::*;

fn py_to_fpvalue(name: &str, value: &Bound<'_, PyAny>) -> PyResult<FpValue> {
    if value.is_none() {
        return Ok(FpValue::Null);
    }
    // bool MUST precede int: in Python `bool` is a subclass of `int`.
    if value.is_instance_of::<PyBool>() {
        return Ok(FpValue::Bool(value.extract()?));
    }
    if value.is_instance_of::<PyInt>() {
        // Via Python str() so arbitrary-precision ints match the reference.
        return Ok(FpValue::Int(value.str()?.extract()?));
    }
    if value.is_instance_of::<PyFloat>() {
        let x: f64 = value.extract()?;
        if !x.is_finite() {
            return Err(PyValueError::new_err(format!(
                "field {name:?}: non-finite float {x} is not canonicalizable"
            )));
        }
        return Ok(FpValue::Float(x));
    }
    if value.is_instance_of::<PyString>() {
        return Ok(FpValue::Str(value.extract()?));
    }
    if value.is_instance_of::<PyBytes>() {
        return Ok(FpValue::Bytes(value.downcast::<PyBytes>()?.as_bytes().to_vec()));
    }
    Err(PyTypeError::new_err(format!(
        "field {name:?}: unsupported value type (v1 record fingerprint is \
         primitive-only: None/bool/int/float/str/bytes)"
    )))
}

/// Canonical SHA-256 fingerprint of a record's content fields.
///
/// `record` is a `dict[str, scalar]`. `__`-prefixed keys are dropped. Returns
/// 64 lowercase hex chars. Raises `TypeError` for non-string keys or
/// non-primitive values, `ValueError` for NaN/Inf floats.
#[pyfunction]
pub fn record_fingerprint(record: &Bound<'_, PyDict>) -> PyResult<String> {
    let mut fields: Vec<(String, FpValue)> = Vec::with_capacity(record.len());
    for (k, v) in record.iter() {
        let name: String = k
            .extract()
            .map_err(|_| PyTypeError::new_err("record field names must be strings"))?;
        if name.starts_with("__") {
            continue;
        }
        let fv = py_to_fpvalue(&name, &v)?;
        fields.push((name, fv));
    }
    fingerprint_fields(fields).map_err(PyValueError::new_err)
}

/// Bulk canonical SHA-256 fingerprints for N records.
///
/// Identical per-record semantics to `record_fingerprint`. Returns one hex
/// string per input record, in order. Errors propagate from the FIRST
/// failing record (matches the per-record loop callers wrote before this
/// kernel landed).
///
/// Two phases:
///   1. Sequential pyo3 extraction — convert each `dict[str, scalar]` to a
///      `Vec<(String, FpValue)>`. Holds the GIL; cannot parallelize.
///   2. GIL-released SHA-256 + hex via rayon. Per-record work is independent
///      so par_iter is correct.
///
/// Spec:
/// docs/superpowers/specs/2026-05-30-bulk-record-fingerprint-kernel-spec.md
/// (gitignored; local design notes).
#[pyfunction]
pub fn record_fingerprints_batch(
    py: Python<'_>,
    records: Vec<Bound<'_, PyDict>>,
) -> PyResult<Vec<String>> {
    // ---- Phase 1: extract all records to FpValue field lists (GIL-held). --
    let mut extracted: Vec<Vec<(String, FpValue)>> = Vec::with_capacity(records.len());
    for record in &records {
        let mut fields: Vec<(String, FpValue)> = Vec::with_capacity(record.len());
        for (k, v) in record.iter() {
            let name: String = k
                .extract()
                .map_err(|_| PyTypeError::new_err("record field names must be strings"))?;
            if name.starts_with("__") {
                continue;
            }
            let fv = py_to_fpvalue(&name, &v)?;
            fields.push((name, fv));
        }
        extracted.push(fields);
    }

    // ---- Phase 2: par_iter SHA-256 + hex (GIL released). -------------------
    // Errors in fingerprint_fields are theoretically possible (canonicalization
    // edge cases) -- collect into Result<Vec, String> via try_fold-equivalent.
    py.allow_threads(|| {
        extracted
            .into_par_iter()
            .map(|fields| fingerprint_fields(fields).map_err(PyValueError::new_err))
            .collect::<PyResult<Vec<String>>>()
    })
}

/// Arrow-native roadmap Phase 3 (#625): bulk fingerprints over Arrow
/// arrays. Takes column names + per-column Arrow arrays (same length),
/// returns one fingerprint per row as a `LargeStringArray`.
///
/// Why a new kernel: `record_fingerprints_batch` benched at 1.19x
/// because per-record dict iteration + pyo3 marshalling caps the win.
/// This path reads each column's Arrow buffer directly -- no Python
/// dict construction, no per-cell pyo3 type checks.
///
/// Supported value types per column: Utf8, LargeUtf8, Int64, Float64,
/// Boolean. Null values map to `FpValue::Null` (same as the dict
/// kernel). Other Arrow types return an error -- the dict kernel
/// supports them via py_to_fpvalue, but the Arrow path explicitly
/// gates on the column dtypes we've validated against the byte-exact
/// canonicalizer.
///
/// Strategic load-bearing for DataFusion B2: kernels that accept Arrow
/// buffers can be wrapped as PyCapsule ScalarUDFs (the Phase 7
/// stretch). This is the second such kernel after `dedup_pairs_arrow`.
#[pyfunction]
pub fn record_fingerprints_batch_arrow(
    py: Python<'_>,
    field_names: Vec<String>,
    field_arrays: Vec<PyArrowType<ArrayData>>,
) -> PyResult<PyArrowType<ArrayData>> {
    if field_names.len() != field_arrays.len() {
        return Err(PyValueError::new_err(format!(
            "record_fingerprints_batch_arrow: field_names ({}) and \
             field_arrays ({}) length mismatch",
            field_names.len(),
            field_arrays.len(),
        )));
    }

    // Filter out `__`-prefixed columns up front; the dict kernel drops
    // them from each record. Keep (name, ArrayKind) for the columns we
    // actually fingerprint.
    let mut cols: Vec<(String, ArrayKind)> = Vec::with_capacity(field_names.len());
    let mut n_rows: Option<usize> = None;
    for (name, arr) in field_names.into_iter().zip(field_arrays.into_iter()) {
        if name.starts_with("__") {
            continue;
        }
        let data = arr.0;
        let len = data.len();
        match n_rows {
            None => n_rows = Some(len),
            Some(expected) if expected != len => {
                return Err(PyValueError::new_err(format!(
                    "record_fingerprints_batch_arrow: column {name:?} has length {len}; \
                     expected {expected} (all columns must be the same length)"
                )));
            }
            _ => {}
        }
        let kind = ArrayKind::from_data(&name, data)?;
        cols.push((name, kind));
    }

    let n_rows = n_rows.unwrap_or(0);

    // Phase 2-equivalent: per-row, materialize the (name, FpValue)
    // field list and compute SHA-256. par_iter under allow_threads
    // because rows are independent and Arrow reads release the GIL
    // (the buffers are owned by us at this point).
    py.allow_threads(|| -> PyResult<PyArrowType<ArrayData>> {
        let hexes: PyResult<Vec<String>> = (0..n_rows)
            .into_par_iter()
            .map(|row| -> PyResult<String> {
                let mut fields: Vec<(String, FpValue)> = Vec::with_capacity(cols.len());
                for (name, kind) in &cols {
                    let v = kind.value_at(row)?;
                    fields.push((name.clone(), v));
                }
                fingerprint_fields(fields).map_err(PyValueError::new_err)
            })
            .collect();
        let hexes = hexes?;
        let out = LargeStringArray::from(
            hexes.iter().map(|s| Some(s.as_str())).collect::<Vec<_>>(),
        );
        Ok(PyArrowType(out.to_data()))
    })
}

/// Per-column array kind dispatch. Holds the typed array view so the
/// per-row `value_at` call doesn't re-downcast on every row.
enum ArrayKind {
    Utf8(StringArray),
    LargeUtf8(LargeStringArray),
    Int64(Int64Array),
    Float64(Float64Array),
    Boolean(BooleanArray),
}

impl ArrayKind {
    fn from_data(name: &str, data: ArrayData) -> PyResult<Self> {
        match data.data_type() {
            DataType::Utf8 => Ok(Self::Utf8(StringArray::from(data))),
            DataType::LargeUtf8 => Ok(Self::LargeUtf8(LargeStringArray::from(data))),
            DataType::Int64 => Ok(Self::Int64(Int64Array::from(data))),
            DataType::Float64 => Ok(Self::Float64(Float64Array::from(data))),
            DataType::Boolean => Ok(Self::Boolean(BooleanArray::from(data))),
            other => Err(PyValueError::new_err(format!(
                "record_fingerprints_batch_arrow: column {name:?} has \
                 unsupported dtype {other:?}; v1 supports Utf8, LargeUtf8, \
                 Int64, Float64, Boolean"
            ))),
        }
    }

    fn value_at(&self, row: usize) -> PyResult<FpValue> {
        match self {
            Self::Utf8(a) => {
                if a.is_null(row) {
                    Ok(FpValue::Null)
                } else {
                    Ok(FpValue::Str(a.value(row).to_string()))
                }
            }
            Self::LargeUtf8(a) => {
                if a.is_null(row) {
                    Ok(FpValue::Null)
                } else {
                    Ok(FpValue::Str(a.value(row).to_string()))
                }
            }
            Self::Int64(a) => {
                if a.is_null(row) {
                    Ok(FpValue::Null)
                } else {
                    // Match the dict kernel's "via Python str() so arbitrary-
                    // precision ints match the reference" semantics by
                    // formatting as a decimal string.
                    Ok(FpValue::Int(a.value(row).to_string()))
                }
            }
            Self::Float64(a) => {
                if a.is_null(row) {
                    Ok(FpValue::Null)
                } else {
                    let x = a.value(row);
                    if !x.is_finite() {
                        return Err(PyValueError::new_err(format!(
                            "record_fingerprints_batch_arrow: non-finite float \
                             {x} is not canonicalizable"
                        )));
                    }
                    Ok(FpValue::Float(x))
                }
            }
            Self::Boolean(a) => {
                if a.is_null(row) {
                    Ok(FpValue::Null)
                } else {
                    Ok(FpValue::Bool(a.value(row)))
                }
            }
        }
    }
}

/// C ABI: canonical record fingerprint over a JSON object string.
///
/// - `json_utf8`: NUL-terminated UTF-8 JSON object (`{"field": scalar, ...}`).
/// - `out_hex`: caller-provided buffer of **>= 65 bytes**; on success it
///   receives the 64 lowercase hex chars plus a trailing NUL. The output size
///   is fixed (SHA-256), so nothing is allocated across the boundary.
///
/// Returns `0` on success, `1` on any error (null pointer, bad UTF-8, invalid
/// JSON, non-object, unsupported value, non-finite float). Panic-safe.
///
/// # Safety
/// `json_utf8` must point to a valid NUL-terminated C string and `out_hex` to a
/// writable buffer of at least 65 bytes.
#[no_mangle]
pub extern "C" fn gm_record_fingerprint(json_utf8: *const c_char, out_hex: *mut c_char) -> c_int {
    let result = std::panic::catch_unwind(|| {
        if json_utf8.is_null() || out_hex.is_null() {
            return Err(());
        }
        let s = unsafe { CStr::from_ptr(json_utf8) }.to_str().map_err(|_| ())?;
        let hex = fingerprint_json(s).map_err(|_| ())?;
        debug_assert_eq!(hex.len(), 64);
        unsafe {
            std::ptr::copy_nonoverlapping(hex.as_ptr(), out_hex as *mut u8, 64);
            *out_hex.add(64) = 0; // NUL terminator
        }
        Ok(())
    });
    match result {
        Ok(Ok(())) => 0,
        _ => 1,
    }
}
