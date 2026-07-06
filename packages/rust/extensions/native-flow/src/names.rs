//! Arrow shims over goldenflow_core::names. Bytes in, kernel per element,
//! bytes out; GIL released. All logic lives in the core.
use crate::util::{map_str_to_bool, map_str_to_str, map_str_to_str_pair, zip_str_to_str};
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
pub fn name_initials_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(names::name_initials(s))
    })?))
}

#[pyfunction]
pub fn strip_middle_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(names::strip_middle(s))
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

#[pyfunction]
pub fn strip_titles_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(names::strip_titles(s))
    })?))
}

#[pyfunction]
pub fn strip_suffixes_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(names::strip_suffixes(s))
    })?))
}

#[pyfunction]
pub fn name_proper_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(names::name_proper(s))
    })?))
}

#[pyfunction]
pub fn nickname_standardize_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(names::nickname_standardize(s))
    })?))
}

#[pyfunction]
pub fn has_initial_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_bool(py, array.0, |s| {
        Some(names::has_initial(s))
    })?))
}

/// Split `"First Last"` -> `(first_name, last_name)` as a pair of Arrow arrays.
#[pyfunction]
pub fn split_name_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<(PyArrowType<ArrayData>, PyArrowType<ArrayData>)> {
    let (first, last) = map_str_to_str_pair(py, array.0, names::split_name)?;
    Ok((PyArrowType(first), PyArrowType(last)))
}

/// Split `"Last, First"` -> `(first_name, last_name)` as a pair of Arrow arrays.
#[pyfunction]
pub fn split_name_reverse_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<(PyArrowType<ArrayData>, PyArrowType<ArrayData>)> {
    let (first, last) = map_str_to_str_pair(py, array.0, names::split_name_reverse)?;
    Ok((PyArrowType(first), PyArrowType(last)))
}

/// Merge `(first, last)` string arrays -> a single `full_name` array.
#[pyfunction]
pub fn merge_name_arrow(
    py: Python,
    first: PyArrowType<ArrayData>,
    last: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(zip_str_to_str(
        py,
        first.0,
        last.0,
        names::merge_name,
    )?))
}
