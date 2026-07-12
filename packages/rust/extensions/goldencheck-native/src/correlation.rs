//! Arrow-reading shims for the deterministic correlation-stat kernels
//! (`correlation.py`). `pearson_r` takes the numeric pair as Arrow Float64
//! arrays (zero-copy via the C Data Interface); `chi2_contingency_stat` takes
//! the contingency table as a Python-side-flattened `Vec<f64>` plus its shape.
//! Both delegate to the pyo3-free `goldencheck-core`, which owns the arithmetic,
//! the scipy `r` clamp, and the 2x2-only Yates correction.
use arrow::array::{make_array, ArrayData};
use arrow::pyarrow::PyArrowType;
use pyo3::prelude::*;

/// Pearson `r` matching `scipy.stats.pearsonr(x, y)[0]` (clamped into `[-1, 1]`).
/// Both arrays must be Float64 (the Python caller casts the null-dropped pair).
#[pyfunction]
pub fn pearson_r(x: PyArrowType<ArrayData>, y: PyArrowType<ArrayData>) -> f64 {
    let xa = make_array(x.0);
    let ya = make_array(y.0);
    goldencheck_core::pearson_r(xa.as_ref(), ya.as_ref())
}

/// Pearson chi-squared statistic matching `scipy.stats.chi2_contingency(m)[0]`
/// (default `correction=True`, so 2x2 tables get Yates' continuity correction).
/// `values` is the row-major flattened `nrows x ncols` observed-count matrix.
#[pyfunction]
pub fn chi2_contingency_stat(values: Vec<f64>, nrows: usize, ncols: usize) -> f64 {
    goldencheck_core::chi2_contingency_stat(&values, nrows, ncols)
}
