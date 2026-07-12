//! Arrow-free shim for the chi-squared goodness-of-fit kernel
//! (`statistical.py` Benford profiler). The observed counts and expected
//! Benford*total values are small 9-element `Vec<f64>`s built Python-side, so
//! (like `chi2_contingency_stat`) they cross as plain vecs -- no Arrow decode.
//! The pyo3-free `goldencheck-core` owns the statistic and the `gamma_ur`
//! upper-tail p-value.
use pyo3::prelude::*;

/// Chi-squared statistic + upper-tail p-value matching
/// `scipy.stats.chisquare(f_obs=observed, f_exp=expected)` -> `(chi2, pvalue)`.
/// The p-value uses the upper regularized incomplete gamma (scipy's chdtrc), so
/// the tail is accurate for large chi2 / small p.
#[pyfunction]
pub fn chi2_gof(observed: Vec<f64>, expected: Vec<f64>) -> (f64, f64) {
    goldencheck_core::chi2_gof(&observed, &expected)
}
