//! Arrow shim over goldenflow_core::autocorrect. The category-autocorrect
//! algorithm is data-dependent: it takes the column's (value, count) pairs and
//! returns the corrections map as a pair of arrays. All logic lives in the core.
use crate::util::read_opt_strings;
use arrow::array::{make_array, Array, ArrayData, Int64Array, StringBuilder};
use arrow::pyarrow::PyArrowType;
use goldenflow_core::autocorrect;
use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;

/// Build the variant->canonical correction map from `values` (Utf8) + `counts`
/// (Int64) arrays. Returns `(from_arr, to_arr)` -- two Utf8 arrays of equal
/// length, the correction pairs.
#[pyfunction]
#[pyo3(signature = (values, counts, freq_threshold=0.05, match_threshold=85.0))]
pub fn build_canonical_map_arrow(
    py: Python,
    values: PyArrowType<ArrayData>,
    counts: PyArrowType<ArrayData>,
    freq_threshold: f64,
    match_threshold: f64,
) -> PyResult<(PyArrowType<ArrayData>, PyArrowType<ArrayData>)> {
    let vv = read_opt_strings(&values.0)?;
    let carr = make_array(counts.0);
    let ci = carr
        .as_any()
        .downcast_ref::<Int64Array>()
        .ok_or_else(|| PyTypeError::new_err("expected an Arrow Int64 array for counts"))?;
    let cv: Vec<i64> = (0..ci.len())
        .map(|i| if ci.is_null(i) { 0 } else { ci.value(i) })
        .collect();

    let pairs = py.detach(|| {
        let refs: Vec<Option<&str>> = vv.iter().map(|o| o.as_deref()).collect();
        autocorrect::build_canonical_map(&refs, &cv, freq_threshold, match_threshold)
    });

    let mut from_b = StringBuilder::with_capacity(pairs.len(), pairs.len() * 8);
    let mut to_b = StringBuilder::with_capacity(pairs.len(), pairs.len() * 8);
    for (from, to) in &pairs {
        from_b.append_value(from);
        to_b.append_value(to);
    }
    Ok((
        PyArrowType(from_b.finish().into_data()),
        PyArrowType(to_b.finish().into_data()),
    ))
}
