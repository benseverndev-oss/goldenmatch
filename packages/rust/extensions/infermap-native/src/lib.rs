//! infermap-native -- thin PyO3 shim over the pyo3-free `infermap-core`. Takes plain
//! Python lists (no Arrow); returns a tuple the Python host maps to a DetectionResult.

use infermap_core::detect_domain as core_detect;
use pyo3::prelude::*;

/// Domain auto-detection. `columns`: df column names. `domains`: list of
/// (name, deduped hints) in host order. Returns
/// (domain, score, runner_up, runner_up_score, reason).
#[pyfunction]
fn detect_domain(
    columns: Vec<String>,
    domains: Vec<(String, Vec<String>)>,
    min_score: f64,
) -> PyResult<(Option<String>, f64, Option<String>, f64, String)> {
    let d = core_detect(&columns, &domains, min_score);
    Ok((d.domain, d.score, d.runner_up, d.runner_up_score, d.reason))
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    // `self::` qualification is REQUIRED: check_native_symbols._WRAP is
    // `wrap_pyfunction!\(\s*(?:\w+::)+(\w+)` -- the bare `wrap_pyfunction!(detect_domain, m)`
    // form (used by analysis-native) would NOT be scanned, red-ing the gate.
    m.add_function(wrap_pyfunction!(self::detect_domain, m)?)?;
    Ok(())
}
