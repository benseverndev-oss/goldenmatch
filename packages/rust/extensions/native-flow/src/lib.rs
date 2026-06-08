//! `goldenflow._native` / `goldenflow_native._native` — native acceleration
//! kernels (PyO3 extension module) for GoldenFlow.
//!
//! Scope: the international phone family. GoldenFlow's pure-Python transforms
//! resolve the common case with vectorized Polars expressions; these kernels
//! accelerate the *residual* (numbers the Polars fast path can't normalize —
//! international formats, non-NANP regions) that would otherwise hit the
//! `phonenumbers` library one row at a time. Each kernel returns null for rows
//! it can't resolve, so the Python reference settles those and the native path
//! is never worse. Mirrors packages/rust/extensions/native (goldenmatch).

use pyo3::prelude::*;

mod phone;
mod util;

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(phone::phone_e164_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(phone::phone_national_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(phone::phone_country_code_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(phone::phone_valid_arrow, m)?)?;
    Ok(())
}
