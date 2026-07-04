//! Arrow shims over goldenflow_core::numeric. Bytes/floats in, kernel per
//! element, floats out; GIL released. All logic lives in the core.
use crate::util::{map_f64_to_f64, map_str_to_f64, map_str_to_i64};
use arrow::array::ArrayData;
use arrow::pyarrow::PyArrowType;
use goldenflow_core::numeric;
use pyo3::prelude::*;

#[pyfunction]
pub fn currency_strip_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_f64(
        py,
        array.0,
        numeric::currency_strip,
    )?))
}

#[pyfunction]
pub fn percentage_normalize_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_f64(
        py,
        array.0,
        numeric::percentage_normalize,
    )?))
}

#[pyfunction]
pub fn to_integer_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_i64(
        py,
        array.0,
        numeric::to_integer,
    )?))
}

#[pyfunction]
pub fn comma_decimal_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_f64(
        py,
        array.0,
        numeric::comma_decimal,
    )?))
}

#[pyfunction]
pub fn scientific_to_decimal_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_f64(
        py,
        array.0,
        numeric::scientific_to_decimal,
    )?))
}

#[pyfunction]
#[pyo3(signature = (array, n=2))]
pub fn round_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
    n: i32,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_f64_to_f64(py, array.0, |v| {
        v.map(|x| numeric::round_f64(x, n))
    })?))
}

#[pyfunction]
#[pyo3(signature = (array, min_val=0.0, max_val=1.0))]
pub fn clamp_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
    min_val: f64,
    max_val: f64,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_f64_to_f64(py, array.0, |v| {
        v.map(|x| numeric::clamp_f64(x, min_val, max_val))
    })?))
}

#[pyfunction]
pub fn abs_value_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_f64_to_f64(py, array.0, |v| {
        v.map(numeric::abs_f64)
    })?))
}

#[pyfunction]
pub fn fill_zero_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_f64_to_f64(py, array.0, |v| {
        Some(numeric::fill_zero(v))
    })?))
}
