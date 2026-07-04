//! Arrow shims over goldenflow_core::address. Bytes in, kernel per element,
//! bytes out; GIL released. All logic lives in the core.
use crate::util::{map_str_to_str, map_str_to_str_quad};
use arrow::array::ArrayData;
use arrow::pyarrow::PyArrowType;
use goldenflow_core::address;
use pyo3::prelude::*;

#[pyfunction]
pub fn address_standardize_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(address::address_standardize(s))
    })?))
}

#[pyfunction]
pub fn address_expand_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(address::address_expand(s))
    })?))
}

#[pyfunction]
pub fn state_abbreviate_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(address::state_abbreviate(s))
    })?))
}

#[pyfunction]
pub fn state_expand_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(address::state_expand(s))
    })?))
}

#[pyfunction]
pub fn zip_normalize_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(address::zip_normalize(s))
    })?))
}

#[pyfunction]
pub fn country_standardize_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(address::country_standardize(s))
    })?))
}

#[pyfunction]
pub fn unit_normalize_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(address::unit_normalize(s))
    })?))
}

/// Parse `"street, city, state zip"` -> `(street, city, state, zip)` as four
/// Arrow arrays. `street` is always present on a non-null row; `city`/`state`/
/// `zip` are null on a no-match row (only the street parsed).
#[pyfunction]
#[allow(clippy::type_complexity)]
pub fn split_address_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<(
    PyArrowType<ArrayData>,
    PyArrowType<ArrayData>,
    PyArrowType<ArrayData>,
    PyArrowType<ArrayData>,
)> {
    let (street, city, state, zip) = map_str_to_str_quad(py, array.0, address::split_address)?;
    Ok((
        PyArrowType(street),
        PyArrowType(city),
        PyArrowType(state),
        PyArrowType(zip),
    ))
}
