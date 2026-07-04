//! Arrow shims over goldenflow_core::categorical. Bytes in, kernel per
//! element, bytes out; GIL released. All logic lives in the core.
//!
//! `category_normalize_key_arrow` is the ONLY symbol needed by the two
//! mapping-based transforms (`category_standardize` / `category_from_file`)
//! -- it accelerates the key-normalization step; the dict lookup itself
//! stays in Python since the mapping is runtime data, not a kernel input.
use crate::util::{map_str_to_bool, map_str_to_str};
use arrow::array::ArrayData;
use arrow::pyarrow::PyArrowType;
use goldenflow_core::categorical;
use pyo3::prelude::*;

#[pyfunction]
pub fn boolean_normalize_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_bool(
        py,
        array.0,
        categorical::boolean_normalize,
    )?))
}

#[pyfunction]
pub fn gender_standardize_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(categorical::gender_standardize(s))
    })?))
}

#[pyfunction]
pub fn null_standardize_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(
        py,
        array.0,
        categorical::null_standardize,
    )?))
}

#[pyfunction]
pub fn category_normalize_key_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(categorical::category_normalize_key(s))
    })?))
}
