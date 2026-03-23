//! Error types for the GoldenMatch bridge.

use thiserror::Error;

#[derive(Error, Debug)]
pub enum BridgeError {
    #[error("Python import error: {0}")]
    PythonImport(String),

    #[error("Python runtime error: {0}")]
    PythonRuntime(String),

    #[error("Arrow conversion error: {0}")]
    ArrowConversion(String),

    #[error("Invalid configuration: {0}")]
    InvalidConfig(String),
}

impl From<pyo3::PyErr> for BridgeError {
    fn from(err: pyo3::PyErr) -> Self {
        BridgeError::PythonRuntime(err.to_string())
    }
}

impl From<arrow::error::ArrowError> for BridgeError {
    fn from(err: arrow::error::ArrowError) -> Self {
        BridgeError::ArrowConversion(err.to_string())
    }
}
