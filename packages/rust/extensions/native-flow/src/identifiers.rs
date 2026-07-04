//! Arrow shims over goldenflow_core::identifiers. Bytes in, kernel per element,
//! bytes out; GIL released. All logic lives in the core.
use crate::util::{map_str_to_bool, map_str_to_str};
use arrow::array::ArrayData;
use arrow::pyarrow::PyArrowType;
use goldenflow_core::identifiers::{ean, iban, isbn, luhn, swift, vat};
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

#[pyfunction]
pub fn iban_validate_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_bool(py, array.0, |s| {
        Some(iban::iban_validate(s))
    })?))
}
#[pyfunction]
pub fn iban_format_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, iban::iban_format)?))
}

#[pyfunction]
pub fn isbn_validate_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_bool(py, array.0, |s| {
        Some(isbn::isbn_validate(s))
    })?))
}
#[pyfunction]
pub fn isbn_normalize_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(
        py,
        array.0,
        isbn::isbn_normalize,
    )?))
}

#[pyfunction]
pub fn ean_validate_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_bool(py, array.0, |s| {
        Some(ean::ean_validate(s))
    })?))
}

#[pyfunction]
pub fn swift_validate_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_bool(py, array.0, |s| {
        Some(swift::swift_validate(s))
    })?))
}
#[pyfunction]
pub fn swift_format_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(
        py,
        array.0,
        swift::swift_format,
    )?))
}

#[pyfunction]
pub fn vat_validate_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_bool(py, array.0, |s| {
        Some(vat::vat_validate(s))
    })?))
}
#[pyfunction]
pub fn vat_format_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, vat::vat_format)?))
}
