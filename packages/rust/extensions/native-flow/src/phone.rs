//! International phone kernel — a Rust port of libphonenumber (`phonenumber`
//! crate) behind an Arrow zero-copy surface.
//!
//! Each function returns null for any row it cannot resolve (parse error), so
//! the Python caller falls back to the `phonenumbers` library for that row and
//! the native path is never *worse* than pure Python. The parity gate
//! (`tests/transforms/test_native_parity.py`) asserts native == `phonenumbers`
//! on every row native does resolve, for the installed metadata version.
//!
//! `nanp_only`: when true, emit a result ONLY for NANP numbers (country calling
//! code 1) and null for everything else. This is the parity-safe mode the gated
//! default uses. The Rust port is byte-identical to Python `phonenumbers` on
//! NANP, but with a non-matching default region (the transforms pass "US") it
//! mis-strips a leading national "1" on some `+CC` international numbers
//! (e.g. `+33142685300` -> `+3342685300`). Restricting native to country-code-1
//! results sidesteps that entirely; international rows fall back to Python.

use crate::util::{map_str_to_bool, map_str_to_i64, map_str_to_str};
use arrow::array::ArrayData;
use arrow::pyarrow::PyArrowType;
use phonenumber::{country, Mode, PhoneNumber};
use pyo3::prelude::*;

fn region_of(region: &str) -> Option<country::Id> {
    region.parse::<country::Id>().ok()
}

fn parse(region: Option<country::Id>, s: &str) -> Option<phonenumber::PhoneNumber> {
    phonenumber::parse(region, s).ok()
}

/// Parse, then drop the result (-> None) when `nanp_only` is set and the number
/// is not country calling code 1. Keeps native authoritative only where it is
/// proven byte-identical to Python.
fn parse_gated(region: Option<country::Id>, s: &str, nanp_only: bool) -> Option<PhoneNumber> {
    let n = parse(region, s)?;
    if nanp_only && n.country().code() != 1 {
        return None;
    }
    Some(n)
}

#[pyfunction]
#[pyo3(signature = (array, region, nanp_only=false))]
pub fn phone_e164_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
    region: &str,
    nanp_only: bool,
) -> PyResult<PyArrowType<ArrayData>> {
    let reg = region_of(region);
    let out = map_str_to_str(py, array.0, move |s| {
        parse_gated(reg, s, nanp_only).map(|n| n.format().mode(Mode::E164).to_string())
    })?;
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
    let reg = region_of(region);
    let out = map_str_to_str(py, array.0, move |s| {
        parse_gated(reg, s, nanp_only).map(|n| n.format().mode(Mode::National).to_string())
    })?;
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
    let reg = region_of(region);
    let out = map_str_to_i64(py, array.0, move |s| {
        parse_gated(reg, s, nanp_only).map(|n| i64::from(n.country().code()))
    })?;
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
    let reg = region_of(region);
    // Distinguish "parsed, definitely invalid" (-> false) from "couldn't parse"
    // (-> null, so Python decides). `phone_validate` in Python returns False on
    // parse failure, but leaving it null keeps the native path strictly
    // non-authoritative and lets the reference settle it.
    let out = map_str_to_bool(py, array.0, move |s| {
        parse_gated(reg, s, nanp_only).map(|n| phonenumber::is_valid(&n))
    })?;
    Ok(PyArrowType(out))
}
