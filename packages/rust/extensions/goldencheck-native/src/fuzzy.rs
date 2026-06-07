//! Shim for the fuzzy near-duplicate value-clustering kernel.
use pyo3::prelude::*;

/// Cluster the distinct `values` of a column into groups of edit-distance-close
/// strings (inconsistent encodings of the same thing). Returns clusters as
/// lists of indices into `values`; clusters have size >= 2.
///
/// Takes a plain `list[str]` (a column's distinct values are a small set, so the
/// Arrow C Data Interface buys nothing here) and delegates to
/// `goldencheck_core::near_duplicate_clusters`.
#[pyfunction]
#[pyo3(signature = (values, min_similarity))]
pub fn near_duplicate_value_clusters(
    values: Vec<String>,
    min_similarity: f64,
) -> PyResult<Vec<Vec<usize>>> {
    Ok(goldencheck_core::near_duplicate_clusters(
        &values,
        min_similarity,
    ))
}
