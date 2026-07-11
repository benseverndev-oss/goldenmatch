//! Shim for the fuzzy near-duplicate value-clustering kernel.
//!
//! Python callers (`cell_quality.py`, `core/kernels.py`,
//! `profilers/fuzzy_values.py`) all pass a plain `list[str]` here today (a
//! column's distinct values are a small set, so the Arrow C Data Interface
//! buys nothing on this path) -- so the pyfunction signature stays `Vec<String>`
//! and delegates to the core's `_slice` entry point, which is also what
//! `goldencheck-wasm` and the `goldenmatch_pg` Postgres extension call.
use pyo3::prelude::*;

#[pyfunction]
#[pyo3(signature = (values, min_similarity))]
pub fn near_duplicate_value_clusters(
    values: Vec<String>,
    min_similarity: f64,
) -> PyResult<Vec<Vec<usize>>> {
    Ok(goldencheck_core::near_duplicate_clusters_slice(
        &values,
        min_similarity,
    ))
}
