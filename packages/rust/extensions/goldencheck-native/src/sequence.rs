//! Arrow-reading shim for the sequence-gap kernel (sequence_detection).
use arrow::array::{make_array, ArrayData};
use arrow::pyarrow::PyArrowType;
use pyo3::prelude::*;

/// Order-preserving sequence-gap analysis matching the `sequence_detection`
/// profiler's Polars computation. Returns
/// `(n_diffs, unit_diff_count, positive_diff_count, is_sorted, min, max,
/// present_size, gap_count, gap_sample)`, or `None` when the array is not
/// Int*/UInt* or has `< 2` non-null values (mirroring the profiler's dtype gate
/// + `total < 2` early return). Delegates to
/// `goldencheck_core::sequence_analysis`, which owns the downcast + null
/// handling + `wrapping_sub` diff + gap scan.
#[pyfunction]
pub fn sequence_analysis(
    array: PyArrowType<ArrayData>,
) -> Option<(usize, usize, usize, bool, i64, i64, usize, usize, Vec<i64>)> {
    let arr = make_array(array.0);
    goldencheck_core::sequence_analysis(arr.as_ref()).map(|s| {
        (
            s.n_diffs,
            s.unit_diff_count,
            s.positive_diff_count,
            s.is_sorted,
            s.min,
            s.max,
            s.present_size,
            s.gap_count,
            s.gap_sample,
        )
    })
}
