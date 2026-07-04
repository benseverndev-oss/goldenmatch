//! Arrow shims over goldenflow_core::email. Bytes in, kernel per element,
//! bytes out; GIL released. All logic lives in the core.
use crate::util::{map_str_to_bool, map_str_to_str};
use arrow::array::ArrayData;
use arrow::pyarrow::PyArrowType;
use goldenflow_core::email;
use pyo3::prelude::*;

#[pyfunction]
pub fn email_lowercase_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(email::email_lowercase(s))
    })?))
}

#[pyfunction]
pub fn email_normalize_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(email::email_normalize(s))
    })?))
}

#[pyfunction]
pub fn email_extract_domain_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(
        py,
        array.0,
        email::email_extract_domain,
    )?))
}

#[pyfunction]
pub fn email_validate_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_bool(
        py,
        array.0,
        email::email_validate,
    )?))
}
