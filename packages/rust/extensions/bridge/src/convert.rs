//! Data conversion utilities for the GoldenMatch bridge.
//!
//! Two strategies available:
//! - Arrow: Uses Polars' native Arrow IPC for efficient bulk data transfer (preferred)
//! - JSON: Fallback for small data or when Arrow deps unavailable

use crate::error::BridgeError;
use pyo3::prelude::*;

/// Convert a JSON string of records into a Python **pyarrow Table**.
///
/// Arrow-native (no polars): `json.loads` -> `pyarrow.Table.from_pylist`. The
/// goldenmatch core-API surface (dedupe / auto_configure / match + the aux
/// validate/autofix/anomaly/profile/preflight/postflight functions) is
/// arrow-native, so a `pa.Table` feeds every call site directly. This is what
/// lets the bridge lanes run without the `[polars]` extra (D6 zero-polars).
///
/// For table data from Postgres SPI (JSON via `row_to_json`), `from_pylist`
/// infers the schema the same way `pl.read_json` did.
pub fn json_to_arrow_df(py: Python<'_>, json_records: &str) -> Result<PyObject, BridgeError> {
    let json_mod = py.import("json")?;
    let pa = py.import("pyarrow")?;

    let records = json_mod.call_method1("loads", (json_records,))?;
    let table = pa
        .getattr("Table")?
        .call_method1("from_pylist", (records,))?;

    Ok(table.into_pyobject(py).unwrap().unbind())
}

/// Convert a pyarrow Table (or a genuine Polars DataFrame) to a JSON string of records.
///
/// Arrow-native (no polars): a `pa.Table` goes `to_pylist` -> `json.dumps`. A
/// genuine polars frame (which still has `write_json`, e.g. when a caller passes
/// one on the polars lane) passes through `write_json` unchanged for
/// byte-identical output. `DedupeResult.golden` / `MatchResult.matched` are
/// pyarrow Tables since v3.0.0, so the arrow branch is the default.
pub fn arrow_df_to_json(py: Python<'_>, df: &PyObject) -> Result<String, BridgeError> {
    let bound = df.bind(py);
    if bound.hasattr("write_json")? {
        // Genuine polars frame -> byte-identical legacy path (polars present).
        let json_bytes = bound.call_method0("write_json")?;
        let json_str: String = json_bytes.extract()?;
        return Ok(json_str);
    }
    // pa.Table -> list[dict] -> JSON array of record objects.
    let json_mod = py.import("json")?;
    let records = bound.call_method0("to_pylist")?;
    let json_str: String = json_mod.call_method1("dumps", (records,))?.extract()?;
    Ok(json_str)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_json_roundtrip() {
        pyo3::prepare_freethreaded_python();

        Python::with_gil(|py| {
            // Arrow-native path: needs pyarrow (a hard goldenmatch dep), NOT polars.
            if py.import("pyarrow").is_err() {
                eprintln!("Skipping test (pyarrow not installed)");
                return;
            }

            let json = r#"[{"name": "John", "email": "j@x.com"}, {"name": "Jane", "email": "jane@y.com"}]"#;
            let df = json_to_arrow_df(py, json).unwrap();
            // Proof the frame is arrow, not polars.
            let ty: String = df.bind(py).get_type().name().unwrap().extract().unwrap();
            assert_eq!(ty, "Table", "json_to_arrow_df must return a pyarrow Table");

            let back = arrow_df_to_json(py, &df).unwrap();
            assert!(back.contains("John"));
            assert!(back.contains("Jane"));
        });
    }
}
