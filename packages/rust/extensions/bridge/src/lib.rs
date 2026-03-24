//! GoldenMatch Bridge -- Python embedding layer for SQL extensions.
//!
//! This crate provides the shared Python bridge used by both the Postgres
//! and DuckDB extensions. It embeds CPython via pyo3, calls GoldenMatch's
//! Python API, and converts results through Apache Arrow.

pub mod api;
pub mod convert;
pub mod error;

use pyo3::prelude::*;
use std::sync::OnceLock;

static INIT_RESULT: OnceLock<Result<(), String>> = OnceLock::new();

/// Initialize the Python interpreter (once per process).
/// Returns Ok(()) if goldenmatch is importable, Err otherwise.
pub fn init() -> Result<(), error::BridgeError> {
    let result = INIT_RESULT.get_or_init(|| {
        pyo3::prepare_freethreaded_python();

        Python::with_gil(|py| match py.import("goldenmatch") {
            Ok(gm) => {
                match gm.getattr("__version__") {
                    Ok(ver) => {
                        let version: String = ver.extract().unwrap_or_default();
                        eprintln!("goldenmatch-bridge: loaded goldenmatch {}", version);
                    }
                    Err(_) => {
                        eprintln!("goldenmatch-bridge: loaded goldenmatch (unknown version)");
                    }
                }
                Ok(())
            }
            Err(e) => Err(format!(
                "Could not import goldenmatch: {}. Install with: pip install goldenmatch>=1.1.0",
                e
            )),
        })
    });

    match result {
        Ok(()) => Ok(()),
        Err(msg) => Err(error::BridgeError::PythonImport(msg.clone())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_init_loads_goldenmatch() {
        // This test requires goldenmatch to be installed in the Python environment
        // that pyo3 links against. Skip gracefully if not available.
        match init() {
            Ok(()) => {
                Python::with_gil(|py| {
                    let gm = py.import("goldenmatch").unwrap();
                    let ver: String = gm.getattr("__version__").unwrap().extract().unwrap();
                    assert!(!ver.is_empty());
                });
            }
            Err(e) => {
                eprintln!("Skipping test (goldenmatch not installed): {}", e);
            }
        }
    }
}
