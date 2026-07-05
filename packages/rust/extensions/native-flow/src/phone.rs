//! Arrow zero-copy surface over `goldenflow_core::phone`. Bytes in, call the
//! owned kernel per element, bytes out — GIL released around the loop. All
//! phone computation lives in goldenflow-core; this file only marshals.
use crate::util::{map_str_to_bool, map_str_to_i64, map_str_to_str};
use arrow::array::ArrayData;
use arrow::pyarrow::PyArrowType;
use goldenflow_core::phone;
use pyo3::prelude::*;

#[pyfunction]
pub fn phone_digits_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, phone::phone_digits)?))
}

#[pyfunction]
#[pyo3(signature = (array, region, nanp_only=false))]
pub fn phone_e164_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
    region: &str,
    nanp_only: bool,
) -> PyResult<PyArrowType<ArrayData>> {
    let reg = phone::region_of(region);
    let out = map_str_to_str(py, array.0, move |s| phone::e164(reg, s, nanp_only))?;
    Ok(PyArrowType(out))
}

#[pyfunction]
#[pyo3(signature = (array, region, nanp_only=false))]
pub fn phone_national_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
    region: &str,
    nanp_only: bool,
) -> PyResult<PyArrowType<ArrayData>> {
    let reg = phone::region_of(region);
    let out = map_str_to_str(py, array.0, move |s| phone::national(reg, s, nanp_only))?;
    Ok(PyArrowType(out))
}

#[pyfunction]
#[pyo3(signature = (array, region, nanp_only=false))]
pub fn phone_country_code_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
    region: &str,
    nanp_only: bool,
) -> PyResult<PyArrowType<ArrayData>> {
    let reg = phone::region_of(region);
    let out = map_str_to_i64(py, array.0, move |s| phone::country_code(reg, s, nanp_only))?;
    Ok(PyArrowType(out))
}

#[pyfunction]
#[pyo3(signature = (array, region, nanp_only=false))]
pub fn phone_valid_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
    region: &str,
    nanp_only: bool,
) -> PyResult<PyArrowType<ArrayData>> {
    let reg = phone::region_of(region);
    let out = map_str_to_bool(py, array.0, move |s| phone::valid(reg, s, nanp_only))?;
    Ok(PyArrowType(out))
}
