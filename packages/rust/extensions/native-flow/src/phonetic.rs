//! Arrow shim over `goldenflow_core::phonetic`. Bytes in, kernel per element,
//! bytes out; GIL released. All logic lives in the core.
use crate::util::{map_str_to_str, map_str_to_str_pair};
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

/// Double Metaphone -> (primary, alternate) code pair. Mirrors `split_name`'s
/// two-array marshaling: a null input nulls both outputs.
#[pyfunction]
pub fn double_metaphone_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<(PyArrowType<ArrayData>, PyArrowType<ArrayData>)> {
    let (primary, alt) = map_str_to_str_pair(py, array.0, phonetic::double_metaphone)?;
    Ok((PyArrowType(primary), PyArrowType(alt)))
}
