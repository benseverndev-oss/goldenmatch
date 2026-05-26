//! Canonical record fingerprint kernel — see
//! `packages/python/goldenmatch/docs/design/2026-05-26-stable-record-hash-cabi-plan.md`.
//!
//! Produces a deterministic, language-agnostic SHA-256 fingerprint of a
//! record's content fields. The digest is SHA-256 (portable + fast everywhere;
//! a hand-rolled hash would just reimplement a tuned lib and violate Gate 1).
//! The *value* of this kernel is the canonicalization, which CPython's
//! `json.dumps(..., sort_keys=True, default=str)` does NOT reproduce across
//! languages (separators, `ensure_ascii`, float repr, `default=str`).
//!
//! Two entry points share one canonicalizer (`fingerprint_fields`):
//! - `record_fingerprint` (PyO3) — takes a Python `dict`;
//! - `gm_record_fingerprint` (C ABI) — takes a JSON object string, for
//!   non-Python callers (pgrx / DuckDB / a future Node/C# SDK). Both map their
//!   input to the same `FpValue` intermediate, so the same logical record
//!   yields the same hash on every surface.
//!
//! Canonicalization v1 — MUST match `goldenmatch.core._hashing._fingerprint_py`
//! byte-for-byte:
//! - drop fields whose name starts with `__`;
//! - sort fields by name (Rust `str` ordering is UTF-8 byte-lexicographic,
//!   which for valid UTF-8 equals Unicode code-point order == Python `sorted`);
//! - for each field append `name 0x1f TAG value 0x1e` to a byte buffer;
//! - per-type TAG + value bytes (type-tagged, so int `1` != str `"1"` != `True`),
//!   any other type raising (v1 is primitive-only):
//!
//! ```text
//! null  -> b'n'   (no value bytes)
//! bool  -> b'b' + b'1' / b'0'
//! int   -> b'i' + base-10 ASCII  (arbitrary precision)
//! float -> b'f' + 16 hex of IEEE-754 big-endian bits; -0.0 -> 0.0; NaN/Inf rejected
//! str   -> b's' + raw UTF-8 bytes
//! bytes -> b'y' + raw bytes  (PyO3 path only; JSON can't carry bytes)
//! ```
//!
//! - SHA-256 the buffer; return 64 lowercase hex chars.
use std::ffi::CStr;
use std::os::raw::{c_char, c_int};

use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyString};
use sha2::{Digest, Sha256};

const US: u8 = 0x1f; // unit separator: between a field name and its value
const RS: u8 = 0x1e; // record separator: end of one field

/// Type-tagged value the canonicalizer accepts. `Int` keeps the decimal string
/// (arbitrary precision); `Float` keeps the f64 (canonicalized via IEEE bits).
enum FpValue {
    Null,
    Bool(bool),
    Int(String),
    Float(f64),
    Str(String),
    Bytes(Vec<u8>),
}

fn to_hex(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

/// The canonicalization spec, shared by every surface. The caller has already
/// dropped `__`-prefixed fields. Returns 64 lowercase hex chars, or an error
/// string for a non-finite float.
fn fingerprint_fields(mut fields: Vec<(String, FpValue)>) -> Result<String, String> {
    fields.sort_by(|a, b| a.0.cmp(&b.0));
    let mut buf: Vec<u8> = Vec::new();
    for (name, value) in &fields {
        buf.extend_from_slice(name.as_bytes());
        buf.push(US);
        match value {
            FpValue::Null => buf.push(b'n'),
            FpValue::Bool(b) => {
                buf.push(b'b');
                buf.push(if *b { b'1' } else { b'0' });
            }
            FpValue::Int(s) => {
                buf.push(b'i');
                buf.extend_from_slice(s.as_bytes());
            }
            FpValue::Float(x) => {
                if !x.is_finite() {
                    return Err(format!(
                        "field {name:?}: non-finite float is not canonicalizable"
                    ));
                }
                let norm = if *x == 0.0 { 0.0_f64 } else { *x }; // collapse -0.0
                buf.push(b'f');
                buf.extend_from_slice(to_hex(&norm.to_bits().to_be_bytes()).as_bytes());
            }
            FpValue::Str(s) => {
                buf.push(b's');
                buf.extend_from_slice(s.as_bytes());
            }
            FpValue::Bytes(b) => {
                buf.push(b'y');
                buf.extend_from_slice(b);
            }
        }
        buf.push(RS);
    }
    Ok(to_hex(&Sha256::digest(&buf)))
}

// ── PyO3 entry point ─────────────────────────────────────────────────────

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
        return Ok(FpValue::Bytes(
            value.downcast::<PyBytes>()?.as_bytes().to_vec(),
        ));
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

// ── C ABI entry point ────────────────────────────────────────────────────

fn json_to_fpvalue(v: &serde_json::Value) -> Result<FpValue, String> {
    use serde_json::Value as J;
    match v {
        J::Null => Ok(FpValue::Null),
        J::Bool(b) => Ok(FpValue::Bool(*b)),
        J::String(s) => Ok(FpValue::Str(s.clone())),
        J::Number(n) => {
            // arbitrary_precision keeps the literal; an int has no '.'/'e'.
            let lit = n.to_string();
            if lit.contains('.') || lit.contains('e') || lit.contains('E') {
                lit.parse::<f64>()
                    .map(FpValue::Float)
                    .map_err(|_| format!("unparseable number {lit}"))
            } else {
                Ok(FpValue::Int(lit))
            }
        }
        J::Array(_) | J::Object(_) => {
            Err("nested arrays/objects are not supported (v1 is primitive-only)".into())
        }
    }
}

fn fingerprint_json(s: &str) -> Result<String, String> {
    let v: serde_json::Value = serde_json::from_str(s).map_err(|e| e.to_string())?;
    let obj = v.as_object().ok_or("top-level JSON must be an object")?;
    let mut fields: Vec<(String, FpValue)> = Vec::with_capacity(obj.len());
    for (k, val) in obj {
        if k.starts_with("__") {
            continue;
        }
        fields.push((k.clone(), json_to_fpvalue(val)?));
    }
    fingerprint_fields(fields)
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
        let s = unsafe { CStr::from_ptr(json_utf8) }
            .to_str()
            .map_err(|_| ())?;
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
