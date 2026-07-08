//! Arrow-native decoding helpers shared by the kernels: column interning to
//! dense `u64` value-ids (for the key/FD kernels) and typed numeric extraction
//! (for Benford). Moved down from the `goldencheck-native` pyo3 shim so the
//! Arrow boundary lives in the pyo3-free core. No pyo3, no Python.
use arrow::array::Array;
use arrow::error::ArrowError;

/// Placeholder to force `arrow` to link in Task 1; replaced in the next task.
#[allow(dead_code)]
pub(crate) fn arrow_linked(a: &dyn Array) -> Result<usize, ArrowError> {
    Ok(a.len())
}
