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
//! Canonicalization v1 — MUST match `goldenmatch.core._hashing._fingerprint_py`
//! byte-for-byte:
//! - drop fields whose name starts with `__`;
//! - sort fields by name (Rust `str` ordering is UTF-8 byte-lexicographic,
//!   which for valid UTF-8 equals Unicode code-point order == Python `sorted`);
//! - for each field append `name 0x1f TAG value 0x1e` to a byte buffer;
//! - per-type TAG + value bytes (type-tagged, so int `1` != str `"1"` != `True`),
//!   any other type raising (v1 is primitive-only; datetime/UUID/Decimal
//!   canonicalization is a documented follow-up):
//!
//! ```text
//! null  -> b'n'   (no value bytes)
//! bool  -> b'b' + b'1' / b'0'
//! int   -> b'i' + base-10 ASCII via Python str()  (arbitrary precision)
//! float -> b'f' + 16 hex of IEEE-754 big-endian bits; -0.0 -> 0.0; NaN/Inf rejected
//! str   -> b's' + raw UTF-8 bytes
//! bytes -> b'y' + raw bytes
//! ```
//!
//! - SHA-256 the buffer; return 64 lowercase hex chars.
use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyString};
use sha2::{Digest, Sha256};

const US: u8 = 0x1f; // unit separator: between a field name and its value
const RS: u8 = 0x1e; // record separator: end of one field

fn to_hex(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

/// Append `TAG + value-bytes` for one value. `name` is only used for error text.
fn append_value(buf: &mut Vec<u8>, name: &str, value: &Bound<'_, PyAny>) -> PyResult<()> {
    if value.is_none() {
        buf.push(b'n');
        return Ok(());
    }
    // bool MUST precede int: in Python `bool` is a subclass of `int`.
    if value.is_instance_of::<PyBool>() {
        buf.push(b'b');
        buf.push(if value.extract::<bool>()? { b'1' } else { b'0' });
        return Ok(());
    }
    if value.is_instance_of::<PyInt>() {
        // Via Python str() so arbitrary-precision ints match the reference.
        buf.push(b'i');
        let s: String = value.str()?.extract()?;
        buf.extend_from_slice(s.as_bytes());
        return Ok(());
    }
    if value.is_instance_of::<PyFloat>() {
        let x: f64 = value.extract()?;
        if !x.is_finite() {
            return Err(PyValueError::new_err(format!(
                "field {name:?}: non-finite float {x} is not canonicalizable"
            )));
        }
        let norm = if x == 0.0 { 0.0_f64 } else { x }; // collapse -0.0 -> 0.0
        buf.push(b'f');
        buf.extend_from_slice(to_hex(&norm.to_bits().to_be_bytes()).as_bytes());
        return Ok(());
    }
    if value.is_instance_of::<PyString>() {
        buf.push(b's');
        let s: String = value.extract()?;
        buf.extend_from_slice(s.as_bytes());
        return Ok(());
    }
    if value.is_instance_of::<PyBytes>() {
        buf.push(b'y');
        let b = value.downcast::<PyBytes>()?;
        buf.extend_from_slice(b.as_bytes());
        return Ok(());
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
    let mut fields: Vec<(String, Bound<'_, PyAny>)> = Vec::with_capacity(record.len());
    for (k, v) in record.iter() {
        let name: String = k
            .extract()
            .map_err(|_| PyTypeError::new_err("record field names must be strings"))?;
        if name.starts_with("__") {
            continue;
        }
        fields.push((name, v));
    }
    fields.sort_by(|a, b| a.0.cmp(&b.0));

    let mut buf: Vec<u8> = Vec::new();
    for (name, value) in &fields {
        buf.extend_from_slice(name.as_bytes());
        buf.push(US);
        append_value(&mut buf, name, value)?;
        buf.push(RS);
    }
    Ok(to_hex(&Sha256::digest(&buf)))
}
