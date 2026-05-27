//! Canonical record fingerprint — the cross-surface stable record-id hash.
//!
//! Pure Rust (no pyo3, no pgrx) so every surface can share ONE canonicalizer:
//! the `native` PyO3 extension wraps it for Python, the C ABI exposes it, and
//! the `postgres` pgrx extension calls it **directly** (no embedded CPython).
//!
//! The digest is SHA-256 (portable + fast everywhere; a hand-rolled hash would
//! just reimplement a tuned lib). The value of this crate is the
//! canonicalization, which CPython's `json.dumps(..., sort_keys=True,
//! default=str)` does NOT reproduce across languages.
//!
//! Spec — MUST match `goldenmatch.core._hashing._fingerprint_py` byte-for-byte:
//! drop `__`-prefixed fields; sort by name (UTF-8 byte order == code-point
//! order == Python `sorted`); append `name 0x1f TAG value 0x1e`; type-tagged
//! values (so int `1` != str `"1"` != `True`):
//!
//! ```text
//! null  -> b'n'   (no value bytes)
//! bool  -> b'b' + b'1' / b'0'
//! int   -> b'i' + base-10 ASCII  (arbitrary precision)
//! float -> b'f' + 16 hex of IEEE-754 big-endian bits; -0.0 -> 0.0; NaN/Inf rejected
//! str   -> b's' + raw UTF-8 bytes
//! bytes -> b'y' + raw bytes
//! ```
//!
//! then SHA-256 the buffer and return 64 lowercase hex chars.
use sha2::{Digest, Sha256};

const US: u8 = 0x1f; // unit separator: between a field name and its value
const RS: u8 = 0x1e; // record separator: end of one field

/// Type-tagged value the canonicalizer accepts. `Int` keeps the decimal string
/// (arbitrary precision); `Float` keeps the f64 (canonicalized via IEEE bits).
pub enum FpValue {
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

/// The canonicalization spec. The caller has already dropped `__`-prefixed
/// fields. Returns 64 lowercase hex chars, or an error string for a non-finite
/// float.
pub fn fingerprint_fields(mut fields: Vec<(String, FpValue)>) -> Result<String, String> {
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

/// Canonical fingerprint of a record given as a JSON object string. Drops
/// `__`-prefixed keys. Returns 64 lowercase hex chars, or an error string for
/// invalid JSON, a non-object, an unsupported value, or a non-finite float.
pub fn fingerprint_json(s: &str) -> Result<String, String> {
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

#[cfg(test)]
mod tests {
    use super::*;

    // Pinned vectors computed from the canonical bytes (independent of impl),
    // identical to tests/test_record_fingerprint.py::_PINNED.
    #[test]
    fn pinned_vectors() {
        assert_eq!(
            fingerprint_json("{}").unwrap(),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(
            fingerprint_json(r#"{"a":"x"}"#).unwrap(),
            "7381d5ba2dac5be0af49232a3209ab8d0dc2e4ed804a60ce533fdfe5254307e3"
        );
        assert_eq!(
            fingerprint_json(r#"{"a":1}"#).unwrap(),
            "b42e38730ddd9a099426dffa93926c03258ee2cd93f75204daa6f989af628206"
        );
        assert_eq!(
            fingerprint_json(r#"{"n":1.5}"#).unwrap(),
            "241b8cd11b575fd2b21e90b490f57fac54930f9a12124f23e284caa200c403a9"
        );
    }

    #[test]
    fn drops_underscore_and_is_type_tagged() {
        assert_eq!(
            fingerprint_json(r#"{"a":1,"__row_id__":9}"#).unwrap(),
            fingerprint_json(r#"{"a":1}"#).unwrap()
        );
        // int 1 != str "1"
        assert_ne!(
            fingerprint_json(r#"{"a":1}"#).unwrap(),
            fingerprint_json(r#"{"a":"1"}"#).unwrap()
        );
    }

    #[test]
    fn rejects_bad_input() {
        assert!(fingerprint_json("not json").is_err());
        assert!(fingerprint_json("[1,2,3]").is_err());
        assert!(fingerprint_json(r#"{"a":[1]}"#).is_err());
    }
}
