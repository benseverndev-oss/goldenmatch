//! Arrow-reading shims for the numeric-stats kernels (range_distribution).
use arrow::array::{make_array, ArrayData};
use arrow::pyarrow::PyArrowType;
use pyo3::prelude::*;

/// Single-pass numeric summary matching Polars `.min()/.max()/.mean()/.std()`
/// (sample std, ddof=1). Returns `(count_nonnull, min, max, mean, std, sum)`.
/// Fields carry `NaN` where Polars would return `None` (empty / all-NaN
/// min-max, or `n<2` std) or where IEEE arithmetic propagates `NaN`. Decodes
/// the pyarrow array and delegates to `goldencheck_core::column_numeric_stats`,
/// which owns the downcast + null handling + NaN/inf parity.
#[pyfunction]
pub fn column_numeric_stats(array: PyArrowType<ArrayData>) -> (usize, f64, f64, f64, f64, f64) {
    let arr = make_array(array.0);
    let s = goldencheck_core::column_numeric_stats(arr.as_ref());
    (s.count_nonnull, s.min, s.max, s.mean, s.std, s.sum)
}

/// Count values strictly outside `[lower, upper]` (matching Polars
/// `filter((s < lower) | (s > upper))`), returning `(count, first-5 sample)`
/// with the sample formatted per the array's native dtype (Int64 -> `"1"`;
/// Float64 -> Python `str(float)` form), in array order. The caller MUST pass
/// the Polars-computed `lower`/`upper` (mean-3*std / mean+3*std) so boundary
/// values agree with the authoritative Polars filter.
#[pyfunction]
pub fn count_outside(
    array: PyArrowType<ArrayData>,
    lower: f64,
    upper: f64,
) -> (usize, Vec<String>) {
    let arr = make_array(array.0);
    goldencheck_core::count_outside(arr.as_ref(), lower, upper)
}
