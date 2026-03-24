//! Arrow <-> Python type conversion utilities.
//!
//! Handles converting between Rust Arrow RecordBatches and Python Polars DataFrames
//! via the Arrow C Data Interface (zero-copy where possible).

use crate::error::BridgeError;
use pyo3::prelude::*;

/// Convert a JSON string of records into a Python Polars DataFrame.
///
/// This is the initial implementation using JSON serialization.
/// Future optimization: use Arrow C Data Interface for near-zero-copy.
pub fn json_to_polars_df(py: Python<'_>, json_records: &str) -> Result<PyObject, BridgeError> {
    let pl = py.import("polars")?;
    let io = py.import("io")?;

    // polars.read_json(io.StringIO(json_records))
    let string_io = io.call_method1("StringIO", (json_records,))?;
    let df = pl.call_method1("read_json", (string_io,))?;

    Ok(df.into_pyobject(py).unwrap().unbind())
}

/// Convert a Python Polars DataFrame to a JSON string of records.
///
/// This is the initial implementation using JSON serialization.
/// Future optimization: use Arrow C Data Interface for near-zero-copy.
pub fn polars_df_to_json(py: Python<'_>, df: &PyObject) -> Result<String, BridgeError> {
    let json_bytes = df.call_method0(py, "write_json")?;
    let json_str: String = json_bytes.extract(py)?;
    Ok(json_str)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_json_roundtrip() {
        pyo3::prepare_freethreaded_python();

        Python::with_gil(|py| {
            // Check if polars is available
            if py.import("polars").is_err() {
                eprintln!("Skipping test (polars not installed)");
                return;
            }

            let json = r#"[{"name": "John", "email": "j@x.com"}, {"name": "Jane", "email": "jane@y.com"}]"#;
            let df = json_to_polars_df(py, json).unwrap();
            let back = polars_df_to_json(py, &df).unwrap();

            // Verify roundtrip preserves data
            assert!(back.contains("John"));
            assert!(back.contains("Jane"));
        });
    }
}
