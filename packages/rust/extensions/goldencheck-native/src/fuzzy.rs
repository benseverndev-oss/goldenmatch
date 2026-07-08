//! Shim for the fuzzy near-duplicate value-clustering kernel.
use arrow::array::{make_array, ArrayData};
use arrow::pyarrow::PyArrowType;
use pyo3::prelude::*;

/// Cluster the distinct values (a null-free Utf8/LargeUtf8 pyarrow array) of a
/// column into edit-distance-close groups. Returns clusters as index lists.
#[pyfunction]
#[pyo3(signature = (values, min_similarity))]
pub fn near_duplicate_value_clusters(
    values: PyArrowType<ArrayData>,
    min_similarity: f64,
) -> PyResult<Vec<Vec<usize>>> {
    let array = make_array(values.0);
    goldencheck_core::near_duplicate_clusters(array.as_ref(), min_similarity)
        .map_err(|e| pyo3::exceptions::PyTypeError::new_err(e.to_string()))
}
