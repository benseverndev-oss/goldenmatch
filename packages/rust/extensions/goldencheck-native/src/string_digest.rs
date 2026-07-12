//! Arrow-reading shim for the fused string-column digest kernel. Decodes the
//! pyarrow array (zero-copy via the C Data Interface) and delegates to
//! `goldencheck_core::string_column_digest`, which owns the downcast, the
//! single-pass null/n_unique accumulation, and the per-pattern regex counting.
use arrow::array::{make_array, ArrayData};
use arrow::pyarrow::PyArrowType;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// Fused single-pass string digest: `(null_count, n_unique, match_counts)` for
/// the given `patterns` (aligned to the input order). `n_unique` matches pyarrow
/// `count_distinct(mode="all")` (nulls count as one distinct); `match_counts[i]`
/// matches `str_match_count(patterns[i])`. A pattern that the `regex` crate
/// cannot compile raises `ValueError` so the caller falls back per-pattern.
#[pyfunction]
pub fn string_column_digest(
    array: PyArrowType<ArrayData>,
    patterns: Vec<String>,
) -> PyResult<(usize, usize, Vec<usize>)> {
    let arr = make_array(array.0);
    let d = goldencheck_core::string_column_digest(arr.as_ref(), &patterns)
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok((d.null_count, d.n_unique, d.match_counts))
}
