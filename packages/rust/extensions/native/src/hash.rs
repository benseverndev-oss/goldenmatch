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

use goldenmatch_fingerprint_core::{fingerprint_fields, fingerprint_json, FpValue};
use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyString};

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
