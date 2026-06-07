//! `goldencheck._native` -- native acceleration kernels (PyO3 extension module).
//!
//! Thin Arrow-reading shims over the pyo3-free `goldencheck-core` crate. Each
//! function is a behaviour-exact replacement for a CPU-bound Python path in the
//! `goldencheck` package; the Python side (`goldencheck/core/_native_loader.py`)
//! selects the native path only when `GOLDENCHECK_NATIVE` opts in, and the
//! pure-Python implementation stays the default and the fallback.
//!
//! Data crosses the boundary as Arrow arrays via the C Data Interface
//! (`PyArrowType<ArrayData>`), zero-copy, mirroring goldenmatch's `native`
//! crate. The shims here never touch business logic -- they decode Arrow into
//! plain slices and delegate to `goldencheck-core`.
use pyo3::prelude::*;

mod fuzzy;
mod keys;
mod profile;

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(profile::benford_leading_digits, m)?)?;
    m.add_function(wrap_pyfunction!(keys::composite_key_search, m)?)?;
    m.add_function(wrap_pyfunction!(keys::functional_dependency_holds, m)?)?;
    m.add_function(wrap_pyfunction!(keys::discover_functional_dependencies, m)?)?;
    m.add_function(wrap_pyfunction!(fuzzy::near_duplicate_value_clusters, m)?)?;
    Ok(())
}
