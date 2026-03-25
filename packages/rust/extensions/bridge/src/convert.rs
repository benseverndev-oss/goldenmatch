//! Data conversion utilities for the GoldenMatch bridge.
//!
//! Two strategies available:
//! - Arrow: Uses Polars' native Arrow IPC for efficient bulk data transfer (preferred)
//! - JSON: Fallback for small data or when Arrow deps unavailable

use crate::error::BridgeError;
use pyo3::prelude::*;

/// Convert a JSON string of records into a Python Polars DataFrame.
///
/// Uses Polars' native JSON reader. For table data from Postgres SPI
/// (which comes as JSON via row_to_json), this is the natural path.
pub fn json_to_polars_df(py: Python<'_>, json_records: &str) -> Result<PyObject, BridgeError> {
    let pl = py.import("polars")?;
    let io = py.import("io")?;

    let string_io = io.call_method1("StringIO", (json_records,))?;
    let df = pl.call_method1("read_json", (string_io,))?;

    Ok(df.into_pyobject(py).unwrap().unbind())
}

/// Convert a Python Polars DataFrame to a JSON string of records.
pub fn polars_df_to_json(py: Python<'_>, df: &PyObject) -> Result<String, BridgeError> {
    let json_bytes = df.call_method0(py, "write_json")?;
    let json_str: String = json_bytes.extract(py)?;
    Ok(json_str)
}

/// Convert a Python Polars DataFrame to Arrow IPC bytes.
///
/// Uses Polars' native `write_ipc` to serialize as Arrow IPC format.
/// This is much faster than JSON for large DataFrames (avoids string
/// conversion for every cell).
pub fn polars_df_to_arrow_ipc(py: Python<'_>, df: &PyObject) -> Result<Vec<u8>, BridgeError> {
    let io = py.import("io")?;
    let buf = io.call_method0("BytesIO")?;
    df.call_method1(py, "write_ipc", (&buf,))?;
    buf.call_method1("seek", (0,))?;
    let bytes: Vec<u8> = buf.call_method0("read")?.extract()?;
    Ok(bytes)
}

/// Convert Arrow IPC bytes to a Python Polars DataFrame.
///
/// Uses Polars' native `read_ipc` to deserialize Arrow IPC format.
pub fn arrow_ipc_to_polars_df(py: Python<'_>, ipc_bytes: &[u8]) -> Result<PyObject, BridgeError> {
    let pl = py.import("polars")?;
    let io = py.import("io")?;

    let buf = io.call_method1("BytesIO", (ipc_bytes,))?;
    let df = pl.call_method1("read_ipc", (buf,))?;

    Ok(df.into_pyobject(py).unwrap().unbind())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_json_roundtrip() {
        pyo3::prepare_freethreaded_python();

        Python::with_gil(|py| {
            if py.import("polars").is_err() {
                eprintln!("Skipping test (polars not installed)");
                return;
            }

            let json = r#"[{"name": "John", "email": "j@x.com"}, {"name": "Jane", "email": "jane@y.com"}]"#;
            let df = json_to_polars_df(py, json).unwrap();
            let back = polars_df_to_json(py, &df).unwrap();

            assert!(back.contains("John"));
            assert!(back.contains("Jane"));
        });
    }

    #[test]
    fn test_arrow_ipc_roundtrip() {
        pyo3::prepare_freethreaded_python();

        Python::with_gil(|py| {
            if py.import("polars").is_err() {
                eprintln!("Skipping test (polars not installed)");
                return;
            }

            // Create a DataFrame via JSON
            let json = r#"[{"name": "John", "score": 0.95}, {"name": "Jane", "score": 0.87}]"#;
            let df = json_to_polars_df(py, json).unwrap();

            // Roundtrip through Arrow IPC
            let ipc_bytes = polars_df_to_arrow_ipc(py, &df).unwrap();
            assert!(!ipc_bytes.is_empty());

            let df2 = arrow_ipc_to_polars_df(py, &ipc_bytes).unwrap();
            let json2 = polars_df_to_json(py, &df2).unwrap();

            assert!(json2.contains("John"));
            assert!(json2.contains("Jane"));
            assert!(json2.contains("0.95"));
        });
    }
}
