//! Arrow shims over goldenflow_core::url. Bytes in, kernel per element, bytes
//! out; GIL released. All logic lives in the core.
use crate::util::map_str_to_str;
use arrow::array::ArrayData;
use arrow::pyarrow::PyArrowType;
use goldenflow_core::url;
use pyo3::prelude::*;

#[pyfunction]
pub fn url_normalize_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(
        py,
        array.0,
        url::url_normalize,
    )?))
}

#[pyfunction]
pub fn url_extract_domain_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(
        py,
        array.0,
        url::url_extract_domain,
    )?))
}
