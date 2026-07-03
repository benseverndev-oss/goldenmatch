//! Arrow shims over goldenflow_core::identifiers. Bytes in, kernel per element,
//! bytes out; GIL released. All logic lives in the core.
use crate::util::{map_str_to_bool, map_str_to_str};
use arrow::array::ArrayData;
use arrow::pyarrow::PyArrowType;
use goldenflow_core::identifiers::luhn;
use pyo3::prelude::*;

#[pyfunction]
pub fn cc_validate_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_bool(py, array.0, |s| {
        Some(luhn::cc_validate(s))
    })?))
}
#[pyfunction]
pub fn cc_format_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, luhn::cc_format)?))
}
#[pyfunction]
pub fn cc_mask_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, luhn::cc_mask)?))
}
