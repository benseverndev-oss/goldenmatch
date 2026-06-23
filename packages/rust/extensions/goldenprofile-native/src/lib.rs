//! `goldenprofile._native` -- PyO3 binding for the pyo3-free
//! `goldenprofile-core` Virtual Fingerprint engine.
//!
//! Deliberately thin: it marshals a JSON request string in and a JSON result
//! string out over the core's single `resolve_json` boundary -- the SAME
//! boundary the WASM and C ABI bindings wrap. No resolution logic lives here, so
//! Python, WASM, and C produce byte-identical clusters by construction. The
//! Python host (`goldengraph.profile`) builds the request dict and parses the
//! response; keeping the boundary as JSON avoids pyo3-version-specific dict
//! marshaling for the nested score breakdowns.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// Resolve a JSON `ResolveRequest` into a JSON `Resolution`. See
/// `goldenprofile_core::ResolveRequest` for the schema:
/// `{"profiles":[{kind,name,category,anchor,attribute}], "embeddings"?: [[f64]],
/// "config"?: {...}}`. Raises `ValueError` on malformed input or a
/// profiles/embeddings length mismatch.
#[pyfunction]
fn resolve_json(request: &str) -> PyResult<String> {
    goldenprofile_core::resolve_json(request).map_err(PyValueError::new_err)
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(resolve_json, m)?)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
