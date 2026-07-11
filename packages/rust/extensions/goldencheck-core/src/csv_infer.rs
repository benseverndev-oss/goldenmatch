//! Owned CSV type-inference kernel -- the Rust source of truth for
//! `goldencheck/engine/csv_infer.py` (the Python reference this MUST agree
//! with byte-for-byte; see that module's docstring for the full contract and
//! `docs/superpowers/plans/` for the wave spec).
//!
//! Contract (per column, over its non-empty cell values), precedence
//! int -> float -> bool -> str:
//! - `""` is always null; a column with zero non-empty values is `str`
//!   (all-null).
//! - int: every value matches `^-?[0-9]+$`, fits `i64`, and is not a
//!   leading-zero multi-digit value (`^-?0[0-9]+$`; "0"/"-0" ARE int).
//! - float: not all-int, every value matches the finite decimal / scientific
//!   regex `^-?[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?$`, none is a leading-zero
//!   multi-digit value (that guard is PURE-DIGIT only -- "01.5" has a dot so
//!   it is NOT rejected, and stays float 1.5), and none is inf/nan/infinity
//!   (case-insensitive). The whole column coerces to `f64`.
//! - bool: every value is true/false case-insensitive.
//! - str: else (values kept as-is).
//!
//! Integers that don't fit `i64` (e.g. "99999999999999999999") match the int
//! regex but fail the bounds check, so they fall through to float and are
//! parsed with `f64::from_str` (lossy for values this large, exactly like
//! Python's `float()` on the same string) -- a documented, deliberate
//! consequence of the int -> float precedence, not a bug.
//!
//! `infer_and_type` is the Arrow-free, pure-Rust entry point (cells already
//! tokenized as `Vec<Vec<String>>`, mirroring the Python reference's
//! signature) -- the entry point for `goldencheck-native`'s pyo3 shim AND any
//! non-Python surface (wasm, direct crate use). `read_csv_bytes` tokenizes
//! raw CSV bytes into the same `(header, cells)` shape via the `csv` crate,
//! trying UTF-8 then falling back to Latin-1 (a direct byte<->codepoint
//! mapping), mirroring the Python reference's `_read_rows`.

use std::sync::OnceLock;

use csv::ReaderBuilder;
use regex::Regex;

fn leading_zero_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^-?0[0-9]+$").unwrap())
}

fn int_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^-?[0-9]+$").unwrap())
}

fn float_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^-?[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?$").unwrap())
}

fn is_int(value: &str) -> bool {
    if !int_re().is_match(value) {
        return false;
    }
    if leading_zero_re().is_match(value) {
        return false;
    }
    value.parse::<i64>().is_ok()
}

fn is_float(value: &str) -> bool {
    if !float_re().is_match(value) {
        return false;
    }
    if leading_zero_re().is_match(value) {
        return false;
    }
    let lowered = value.to_ascii_lowercase();
    let stripped = lowered.strip_prefix('-').unwrap_or(&lowered);
    !matches!(stripped, "nan" | "inf" | "infinity")
}

fn is_bool(value: &str) -> bool {
    let lowered = value.to_ascii_lowercase();
    lowered == "true" || lowered == "false"
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ColType {
    Int,
    Float,
    Bool,
    Str,
}

fn infer_type(non_empty: &[&str]) -> ColType {
    if non_empty.iter().all(|v| is_int(v)) {
        return ColType::Int;
    }
    if non_empty.iter().all(|v| is_float(v)) {
        return ColType::Float;
    }
    if non_empty.iter().all(|v| is_bool(v)) {
        return ColType::Bool;
    }
    ColType::Str
}

/// A single typed column, indexed positionally same as its row.
#[derive(Debug, Clone, PartialEq)]
pub enum TypedColumn {
    Int(Vec<Option<i64>>),
    Float(Vec<Option<f64>>),
    Bool(Vec<Option<bool>>),
    Str(Vec<Option<String>>),
}

/// Apply the owned CSV inference contract to pre-tokenized rows.
///
/// `cells` is a list of rows, each row a list of string cells aligned to
/// `header`. Returns `(column_name, TypedColumn)` pairs in `header` order,
/// with `""` cells mapped to `None` in every branch.
pub fn infer_and_type(cells: &[Vec<String>], header: &[String]) -> Vec<(String, TypedColumn)> {
    let ncols = header.len();
    let mut non_empty_by_col: Vec<Vec<&str>> = vec![Vec::new(); ncols];
    for row in cells {
        for (col_idx, raw) in row.iter().enumerate().take(ncols) {
            if !raw.is_empty() {
                non_empty_by_col[col_idx].push(raw.as_str());
            }
        }
    }

    let type_by_col: Vec<ColType> = non_empty_by_col
        .iter()
        .map(|values| {
            if values.is_empty() {
                ColType::Str
            } else {
                infer_type(values)
            }
        })
        .collect();

    let mut out: Vec<(String, TypedColumn)> = type_by_col
        .iter()
        .map(|t| match t {
            ColType::Int => TypedColumn::Int(Vec::with_capacity(cells.len())),
            ColType::Float => TypedColumn::Float(Vec::with_capacity(cells.len())),
            ColType::Bool => TypedColumn::Bool(Vec::with_capacity(cells.len())),
            ColType::Str => TypedColumn::Str(Vec::with_capacity(cells.len())),
        })
        .zip(header.iter().cloned())
        .map(|(col, name)| (name, col))
        .collect();

    for row in cells {
        for (col_idx, (_, col)) in out.iter_mut().enumerate().take(ncols) {
            let raw = row.get(col_idx).map(String::as_str).unwrap_or("");
            match col {
                TypedColumn::Int(v) => {
                    v.push(if raw.is_empty() {
                        None
                    } else {
                        Some(raw.parse::<i64>().expect("validated int"))
                    });
                }
                TypedColumn::Float(v) => {
                    v.push(if raw.is_empty() {
                        None
                    } else {
                        Some(raw.parse::<f64>().expect("validated float"))
                    });
                }
                TypedColumn::Bool(v) => {
                    v.push(if raw.is_empty() {
                        None
                    } else {
                        Some(raw.eq_ignore_ascii_case("true"))
                    });
                }
                TypedColumn::Str(v) => {
                    v.push(if raw.is_empty() {
                        None
                    } else {
                        Some(raw.to_string())
                    });
                }
            }
        }
    }

    out
}

/// Decode raw bytes as UTF-8, falling back to Latin-1 (each byte maps
/// directly to the Unicode codepoint of the same ordinal, so this never
/// fails) -- mirrors the Python reference's `_read_rows` try/except.
fn decode_bytes(bytes: &[u8]) -> String {
    match std::str::from_utf8(bytes) {
        Ok(s) => s.to_string(),
        Err(_) => bytes.iter().map(|&b| b as char).collect(),
    }
}

/// Tokenize raw CSV bytes into `(header, data_rows)`, first row as header.
/// Empty input returns `(vec![], vec![])`, matching the Python reference.
pub fn read_csv_bytes(bytes: &[u8], delimiter: u8) -> (Vec<String>, Vec<Vec<String>>) {
    let text = decode_bytes(bytes);
    let mut rdr = ReaderBuilder::new()
        .delimiter(delimiter)
        .has_headers(false)
        .flexible(true)
        .from_reader(text.as_bytes());

    let mut rows: Vec<Vec<String>> = Vec::new();
    for result in rdr.records() {
        match result {
            Ok(record) => rows.push(record.iter().map(|s| s.to_string()).collect()),
            Err(_) => continue,
        }
    }
    if rows.is_empty() {
        return (Vec::new(), Vec::new());
    }
    let header = rows.remove(0);
    (header, rows)
}

/// Read + type raw CSV bytes in one call: tokenize then `infer_and_type`.
pub fn read_csv_owned_bytes(bytes: &[u8], delimiter: u8) -> Vec<(String, TypedColumn)> {
    let (header, rows) = read_csv_bytes(bytes, delimiter);
    infer_and_type(&rows, &header)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rows(cells: &[&[&str]]) -> Vec<Vec<String>> {
        cells
            .iter()
            .map(|r| r.iter().map(|s| s.to_string()).collect())
            .collect()
    }

    fn hdr(names: &[&str]) -> Vec<String> {
        names.iter().map(|s| s.to_string()).collect()
    }

    fn col<'a>(out: &'a [(String, TypedColumn)], name: &str) -> &'a TypedColumn {
        &out.iter().find(|(n, _)| n == name).unwrap().1
    }

    #[test]
    fn int_column() {
        let out = infer_and_type(&rows(&[&["1"], &["2"], &["3"]]), &hdr(&["a"]));
        assert_eq!(
            col(&out, "a"),
            &TypedColumn::Int(vec![Some(1), Some(2), Some(3)])
        );
    }

    #[test]
    fn leading_zero_multidigit_stays_str() {
        // Pinned: ["01234","5"] -> str
        let out = infer_and_type(&rows(&[&["01234"], &["5"]]), &hdr(&["z"]));
        assert_eq!(
            col(&out, "z"),
            &TypedColumn::Str(vec![Some("01234".to_string()), Some("5".to_string())])
        );
    }

    #[test]
    fn single_zero_and_negative_zero_are_int() {
        let out = infer_and_type(&rows(&[&["0"], &["1"]]), &hdr(&["a"]));
        assert_eq!(col(&out, "a"), &TypedColumn::Int(vec![Some(0), Some(1)]));

        let out = infer_and_type(&rows(&[&["-0"], &["1"]]), &hdr(&["a"]));
        assert_eq!(col(&out, "a"), &TypedColumn::Int(vec![Some(0), Some(1)]));
    }

    #[test]
    fn int_overflow_falls_to_float() {
        // Pinned: ["99999999999999999999"] -> float
        let out = infer_and_type(&rows(&[&["99999999999999999999"]]), &hdr(&["a"]));
        match col(&out, "a") {
            TypedColumn::Float(v) => assert_eq!(v, &vec![Some(99999999999999999999.0_f64)]),
            other => panic!("expected float, got {other:?}"),
        }
    }

    #[test]
    fn leading_zero_with_dot_is_float_not_str() {
        // Pinned: ["01.5","2.5"] -> float [1.5, 2.5]. The leading-zero guard is
        // pure-digit-only, so "01.5" (has a dot) is NOT rejected.
        let out = infer_and_type(&rows(&[&["01.5"], &["2.5"]]), &hdr(&["f"]));
        assert_eq!(
            col(&out, "f"),
            &TypedColumn::Float(vec![Some(1.5), Some(2.5)])
        );
    }

    #[test]
    fn nan_inf_stay_str() {
        // Pinned: ["nan","1.0"] -> str
        let out = infer_and_type(&rows(&[&["nan"], &["1.0"]]), &hdr(&["x"]));
        assert_eq!(
            col(&out, "x"),
            &TypedColumn::Str(vec![Some("nan".to_string()), Some("1.0".to_string())])
        );

        for bad in ["inf", "-inf", "Infinity", "INF", "NAN"] {
            let out = infer_and_type(&rows(&[&[bad], &["1.0"]]), &hdr(&["x"]));
            assert!(
                matches!(col(&out, "x"), TypedColumn::Str(_)),
                "{bad} should be str"
            );
        }
    }

    #[test]
    fn trailing_dot_and_plus_stay_str() {
        // Pinned: ["5.","1.0"] -> str; "+5" also str (float regex has no `+` sign).
        let out = infer_and_type(&rows(&[&["5."], &["1.0"]]), &hdr(&["x"]));
        assert_eq!(
            col(&out, "x"),
            &TypedColumn::Str(vec![Some("5.".to_string()), Some("1.0".to_string())])
        );

        let out = infer_and_type(&rows(&[&["+5"], &["1.0"]]), &hdr(&["x"]));
        assert!(matches!(col(&out, "x"), TypedColumn::Str(_)));
    }

    #[test]
    fn bool_column() {
        // Pinned-adjacent: bool inference is case-insensitive true/false only.
        let out = infer_and_type(&rows(&[&["true"], &["False"]]), &hdr(&["b"]));
        assert_eq!(
            col(&out, "b"),
            &TypedColumn::Bool(vec![Some(true), Some(false)])
        );
    }

    #[test]
    fn zero_one_are_int_not_bool() {
        // Pinned: ["0","1"] -> int (not bool -- "0"/"1" never mean bool here).
        let out = infer_and_type(&rows(&[&["0"], &["1"]]), &hdr(&["a"]));
        assert_eq!(col(&out, "a"), &TypedColumn::Int(vec![Some(0), Some(1)]));
    }

    #[test]
    fn all_empty_column_is_str_all_none() {
        let out = infer_and_type(&rows(&[&[""], &[""]]), &hdr(&["a"]));
        assert_eq!(col(&out, "a"), &TypedColumn::Str(vec![None, None]));
    }

    #[test]
    fn empty_cells_are_null_in_every_type() {
        let out = infer_and_type(&rows(&[&["1"], &[""], &["3"]]), &hdr(&["a"]));
        assert_eq!(
            col(&out, "a"),
            &TypedColumn::Int(vec![Some(1), None, Some(3)])
        );
    }

    #[test]
    fn mixed_types_fall_to_str() {
        let out = infer_and_type(&rows(&[&["1"], &["hello"]]), &hdr(&["a"]));
        assert_eq!(
            col(&out, "a"),
            &TypedColumn::Str(vec![Some("1".to_string()), Some("hello".to_string())])
        );
    }

    #[test]
    fn i64_bounds() {
        let out = infer_and_type(
            &rows(&[&["9223372036854775807"], &["-9223372036854775808"]]),
            &hdr(&["a"]),
        );
        assert_eq!(
            col(&out, "a"),
            &TypedColumn::Int(vec![Some(9223372036854775807), Some(-9223372036854775808)])
        );
    }

    #[test]
    fn read_csv_bytes_roundtrip() {
        let bytes = b"a,b\n1,x\n2,y\n";
        let (header, rows) = read_csv_bytes(bytes, b',');
        assert_eq!(header, vec!["a".to_string(), "b".to_string()]);
        assert_eq!(
            rows,
            vec![
                vec!["1".to_string(), "x".to_string()],
                vec!["2".to_string(), "y".to_string()],
            ]
        );
    }

    #[test]
    fn read_csv_bytes_empty_input() {
        let (header, rows) = read_csv_bytes(b"", b',');
        assert!(header.is_empty());
        assert!(rows.is_empty());
    }

    #[test]
    fn read_csv_owned_bytes_types_columns() {
        let bytes = b"a,b\n1,true\n2,false\n";
        let out = read_csv_owned_bytes(bytes, b',');
        assert_eq!(col(&out, "a"), &TypedColumn::Int(vec![Some(1), Some(2)]));
        assert_eq!(
            col(&out, "b"),
            &TypedColumn::Bool(vec![Some(true), Some(false)])
        );
    }
}
