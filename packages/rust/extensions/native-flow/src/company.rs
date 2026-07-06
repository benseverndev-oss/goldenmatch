//! Arrow shims over goldenflow_core::company. Bytes in, kernel per element,
//! bytes out; GIL released. All logic lives in the core.
use crate::util::map_str_to_str;
use arrow::array::ArrayData;
use arrow::pyarrow::PyArrowType;
use goldenflow_core::company;
use pyo3::prelude::*;

#[pyfunction]
pub fn company_normalize_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(
        py,
        array.0,
        company::company_normalize,
    )?))
}

#[pyfunction]
pub fn company_strip_legal_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(
        py,
        array.0,
        company::company_strip_legal,
    )?))
}

#[pyfunction]
pub fn company_extract_legal_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(
        py,
        array.0,
        company::company_extract_legal,
    )?))
}
