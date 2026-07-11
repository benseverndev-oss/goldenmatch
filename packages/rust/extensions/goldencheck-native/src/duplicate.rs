//! Arrow-reading shim for the fused exact/near duplicate-row signature scan.
use arrow::array::{make_array, ArrayData, ArrayRef};
use arrow::pyarrow::PyArrowType;
use pyo3::prelude::*;

fn to_arrays(v: Vec<PyArrowType<ArrayData>>) -> Vec<ArrayRef> {
    v.into_iter().map(|a| make_array(a.0)).collect()
}

/// Fused exact/near duplicate-row scan over `field_arrays` (one Arrow array per
/// column). `is_string[c]` is the caller's `dt == pl.Utf8` mask -- it selects
/// the columns normalized for the near-duplicate signature (NEVER inferred from
/// the Arrow dtype: a Polars Categorical arrives as `Dictionary(_, Utf8)` but is
/// not `pl.Utf8`). Returns `(exact_dup_rows, exact_dup_groups, near_dup_rows,
/// near_dup_groups)` -- the four counts the `approx_duplicate` profiler reports.
/// Delegates to `goldencheck_core::duplicate_signatures`.
#[pyfunction]
#[pyo3(signature = (field_arrays, is_string))]
pub fn duplicate_signatures(
    field_arrays: Vec<PyArrowType<ArrayData>>,
    is_string: Vec<bool>,
) -> PyResult<(usize, usize, usize, usize)> {
    let stats = goldencheck_core::duplicate_signatures(&to_arrays(field_arrays), &is_string)
        .map_err(|e| pyo3::exceptions::PyTypeError::new_err(e.to_string()))?;
    Ok((
        stats.exact_dup_rows,
        stats.exact_dup_groups,
        stats.near_dup_rows,
        stats.near_dup_groups,
    ))
}
