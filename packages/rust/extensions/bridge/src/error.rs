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

    /// v2.x #437 surface sync Phase 6A: validation failure for the
    /// `correction_add` shape (missing required field per decision shape,
    /// out-of-range value, etc.). Maps to Postgres ERRCODE_INVALID_PARAMETER_VALUE
    /// when the pgrx layer wraps this error.
    #[error("Validation error: {0}")]
    Validation(String),
}

impl From<pyo3::PyErr> for BridgeError {
    fn from(err: pyo3::PyErr) -> Self {
        BridgeError::PythonRuntime(err.to_string())
    }
}
