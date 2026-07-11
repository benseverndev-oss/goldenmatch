//! Arrow-reading shim for the fused age-vs-DOB mismatch scan.
use arrow::array::{make_array, ArrayData};
use arrow::pyarrow::PyArrowType;
use pyo3::prelude::*;

/// Fused age-vs-DOB mismatch scan for the `age_validation` profiler.
///
/// - `actual`: the age column already `cast(pl.Float64)` (Python-side).
/// - `dob_epoch_days`: the DOB column already parsed to `Date32` (days since
///   1970-01-01, Python-side).
/// - `ref_epoch_days`: `(reference_date - 1970-01-01).days`.
///
/// Returns `(mismatch_count, sample_indices)` where `sample_indices` is the
/// first-5 mismatch row indices in ascending row order (the caller gathers the
/// same rows via `col_series.filter(mismatch_mask).head(5)`). Delegates to
/// `goldencheck_core::age_mismatch`.
#[pyfunction]
#[pyo3(signature = (actual, dob_epoch_days, ref_epoch_days))]
pub fn age_mismatch(
    actual: PyArrowType<ArrayData>,
    dob_epoch_days: PyArrowType<ArrayData>,
    ref_epoch_days: i64,
) -> PyResult<(usize, Vec<usize>)> {
    let actual = make_array(actual.0);
    let dob = make_array(dob_epoch_days.0);
    let stats = goldencheck_core::age_mismatch(actual.as_ref(), dob.as_ref(), ref_epoch_days)
        .map_err(|e| pyo3::exceptions::PyTypeError::new_err(e.to_string()))?;
    Ok((stats.mismatch_count, stats.sample_indices))
}
