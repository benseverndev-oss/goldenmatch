//! Arrow shim over `goldenflow_core::phonetic`. Bytes in, kernel per element,
//! bytes out; GIL released. All logic lives in the core.
use crate::util::map_str_to_str;
use arrow::array::ArrayData;
use arrow::pyarrow::PyArrowType;
use goldenflow_core::phonetic;
use pyo3::prelude::*;

#[pyfunction]
pub fn soundex_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(phonetic::soundex(s))
    })?))
}
