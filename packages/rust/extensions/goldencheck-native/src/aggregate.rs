//! Arrow-reading shim for the fused column-aggregate kernel.
use arrow::array::{make_array, ArrayData};
use arrow::pyarrow::PyArrowType;
use pyo3::prelude::*;

/// One fused pass over an Arrow column: `(len, null_count, n_unique_nonnull,
/// dtype)`. `dtype` is the neutral vocabulary string
/// (`goldencheck.core.frame._neutral_dtype`): `str | int | uint | float |
/// date | datetime | bool | other`. Decodes the pyarrow array and delegates
/// to `goldencheck_core::column_aggregate`, which owns the downcast + null
/// handling + Polars-parity NaN/signed-zero uniqueness canonicalisation.
#[pyfunction]
pub fn column_aggregate(array: PyArrowType<ArrayData>) -> PyResult<(usize, usize, usize, String)> {
    let arr = make_array(array.0);
    let agg = goldencheck_core::column_aggregate(arr.as_ref());
    Ok((
        agg.len,
        agg.null_count,
        agg.n_unique_nonnull,
        agg.dtype.as_str().to_string(),
    ))
}
