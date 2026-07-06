//! infermap-native -- thin PyO3 shim over the pyo3-free `infermap-core`. Takes plain
//! Python lists (no Arrow); returns a tuple the Python host maps to a DetectionResult.

use infermap_core::detect_domain as core_detect;
use pyo3::prelude::*;

/// Domain auto-detection. `columns`: df column names. `domains`: list of
/// (name, deduped hints) in host order. Returns
/// (domain, score, runner_up, runner_up_score, reason).
// The 5-tuple return is the DetectionResult wire shape the Python host maps; clippy's
// type_complexity would rather we alias it, but a pyfunction signature is clearest inline.
#[allow(clippy::type_complexity)]
#[pyfunction]
fn detect_domain(
    columns: Vec<String>,
    domains: Vec<(String, Vec<String>)>,
    min_score: f64,
) -> PyResult<(Option<String>, f64, Option<String>, f64, String)> {
    let d = core_detect(&columns, &domains, min_score);
    Ok((d.domain, d.score, d.runner_up, d.runner_up_score, d.reason))
}

/// Wave 2 name-scorer shims (return the score; the Python class keeps its reasoning).
#[pyfunction]
fn exact_score(a: &str, b: &str) -> PyResult<f64> {
    Ok(infermap_core::exact_score(a, b))
}

#[pyfunction]
fn fuzzy_name_score(a: &str, b: &str) -> PyResult<f64> {
    Ok(infermap_core::fuzzy_name_score(a, b))
}

#[pyfunction]
fn initialism_score(a: &str, b: &str) -> PyResult<Option<f64>> {
    Ok(infermap_core::initialism_score(a, b))
}

/// Wave 3 profile scorer: scalars-only (host computes avg-lengths + abstain).
#[allow(clippy::too_many_arguments)]
#[pyfunction]
fn profile_score(
    src_dtype: &str,
    tgt_dtype: &str,
    src_null: f64,
    tgt_null: f64,
    src_uniq: f64,
    tgt_uniq: f64,
    src_val_count: f64,
    tgt_val_count: f64,
    src_avg_len: f64,
    tgt_avg_len: f64,
) -> PyResult<f64> {
    Ok(infermap_core::profile_score(
        src_dtype, tgt_dtype, src_null, tgt_null, src_uniq, tgt_uniq,
        src_val_count, tgt_val_count, src_avg_len, tgt_avg_len))
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    // `self::` qualification is REQUIRED: check_native_symbols._WRAP is
    // `wrap_pyfunction!\(\s*(?:\w+::)+(\w+)` -- the bare `wrap_pyfunction!(detect_domain, m)`
    // form (used by analysis-native) would NOT be scanned, red-ing the gate.
    m.add_function(wrap_pyfunction!(self::detect_domain, m)?)?;
    m.add_function(wrap_pyfunction!(self::exact_score, m)?)?;
    m.add_function(wrap_pyfunction!(self::fuzzy_name_score, m)?)?;
    m.add_function(wrap_pyfunction!(self::initialism_score, m)?)?;
    m.add_function(wrap_pyfunction!(self::profile_score, m)?)?;
    Ok(())
}
