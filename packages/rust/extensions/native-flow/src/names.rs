//! Arrow shims over goldenflow_core::names. Bytes in, kernel per element,
//! bytes out; GIL released. All logic lives in the core.
use crate::util::map_str_to_str;
use arrow::array::ArrayData;
use arrow::pyarrow::PyArrowType;
use goldenflow_core::names;
use pyo3::prelude::*;

#[pyfunction]
pub fn name_transliterate_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(names::name_transliterate(s))
    })?))
}

#[pyfunction]
pub fn name_script_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(names::name_script(s))
    })?))
}
