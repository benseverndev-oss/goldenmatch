//! Arrow read/build helpers shared by the kernels.
//!
//! Input string arrays are read zero-copy (`value(i)` borrows the Arrow buffer);
//! outputs are built into new Arrow arrays and handed back across the C Data
//! Interface. Each mapper releases the GIL around the compute loop.

use arrow::array::{
    make_array, Array, ArrayData, BooleanBuilder, Float64Array, Float64Builder, Int64Builder,
    LargeStringArray, StringArray, StringBuilder,
};
use pyo3::exceptions::PyTypeError;
use pyo3::prelude::*;

/// Apply `f` over each non-null string element. `f` returns `None` to emit a
/// null (the caller's Python fallback then handles that row).
fn for_each_str<F: FnMut(usize, Option<&str>)>(data: &ArrayData, mut f: F) -> PyResult<()> {
    let arr = make_array(data.clone());
    if let Some(a) = arr.as_any().downcast_ref::<StringArray>() {
        for i in 0..a.len() {
            f(i, if a.is_null(i) { None } else { Some(a.value(i)) });
        }
        Ok(())
    } else if let Some(a) = arr.as_any().downcast_ref::<LargeStringArray>() {
        for i in 0..a.len() {
            f(i, if a.is_null(i) { None } else { Some(a.value(i)) });
        }
        Ok(())
    } else {
        Err(PyTypeError::new_err(
            "expected an Arrow Utf8 or LargeUtf8 array",
        ))
    }
}

/// Read a Utf8/LargeUtf8 array into an owned `Vec<Option<String>>` (used by
/// multi-array kernels like `build_canonical_map` that need the full column).
pub fn read_opt_strings(data: &ArrayData) -> PyResult<Vec<Option<String>>> {
    let mut out = Vec::new();
    for_each_str(data, |_, v| out.push(v.map(|s| s.to_string())))?;
    Ok(out)
}

/// Columnar generic map (feature-parity with `map_str_to_str` for a
/// String-producing kernel, but ~4-5x faster): `f` writes each element's
/// transformed bytes into ONE shared buffer via
/// `goldenflow_core::columnar::map_str_columnar` (no per-row `String` alloc).
/// Handles BOTH `Utf8` and `LargeUtf8` (the latter is what Polars actually
/// exports, so this arm is the one that fires in production); any other array
/// type falls back to the scalar `map_str_to_str` with the equivalent `scalar`
/// kernel. Byte-identical output either way.
pub fn map_str_columnar<F, G>(py: Python, data: ArrayData, f: F, scalar: G) -> PyResult<ArrayData>
where
    F: Fn(&str, &mut String) + Sync,
    G: Fn(&str) -> String + Sync,
{
    let arr = make_array(data.clone());
    if let Some(s) = arr.as_any().downcast_ref::<StringArray>() {
        Ok(py.detach(|| goldenflow_core::columnar::map_str_columnar(s, &f).into_data()))
    } else if let Some(s) = arr.as_any().downcast_ref::<LargeStringArray>() {
        Ok(py.detach(|| goldenflow_core::columnar::map_str_columnar(s, &f).into_data()))
    } else {
        map_str_to_str(py, data, |x| Some(scalar(x)))
    }
}

/// Columnar ASCII case-fold (~9-10x for the all-ASCII common case): an array
/// whose values buffer is entirely ASCII is folded in one
/// `make_ascii_{lower,upper}case` pass reusing offsets+nulls; non-ASCII falls back
/// to the scalar Unicode `scalar` kernel per element. Handles BOTH `Utf8` and
/// `LargeUtf8` (Polars exports LargeUtf8); any other array type uses the scalar
/// path. Byte-identical output to the scalar path in every case.
pub fn ascii_case_columnar<G>(
    py: Python,
    data: ArrayData,
    upper: bool,
    scalar: G,
) -> PyResult<ArrayData>
where
    G: Fn(&str) -> String + Sync,
{
    let arr = make_array(data.clone());
    if let Some(s) = arr.as_any().downcast_ref::<StringArray>() {
        Ok(py.detach(|| goldenflow_core::columnar::ascii_case(s, upper, &scalar).into_data()))
    } else if let Some(s) = arr.as_any().downcast_ref::<LargeStringArray>() {
        Ok(py.detach(|| goldenflow_core::columnar::ascii_case(s, upper, &scalar).into_data()))
    } else {
        map_str_to_str(py, data, |x| Some(scalar(x)))
    }
}

pub fn map_str_to_str<F>(py: Python, data: ArrayData, f: F) -> PyResult<ArrayData>
where
    F: Fn(&str) -> Option<String> + Sync,
{
    let len = make_array(data.clone()).len();
    let mut builder = StringBuilder::with_capacity(len, len * 12);
    py.detach(|| -> PyResult<()> {
        for_each_str(&data, |_, v| match v {
            Some(s) => match f(s) {
                Some(out) => builder.append_value(out),
                None => builder.append_null(),
            },
            None => builder.append_null(),
        })
    })?;
    Ok(builder.finish().into_data())
}

/// Apply `f` over each non-null string element, producing a PAIR of outputs
/// (e.g. `split_name` -> first + last). A null input emits a null in BOTH output
/// arrays; a non-null input emits both halves of `f`'s `(String, String)`
/// (either half may be empty, but never null on a present row -- matching the
/// Python transforms, which only null a row when the input is null).
pub fn map_str_to_str_pair<F>(py: Python, data: ArrayData, f: F) -> PyResult<(ArrayData, ArrayData)>
where
    F: Fn(&str) -> (String, String) + Sync,
{
    let len = make_array(data.clone()).len();
    let mut a = StringBuilder::with_capacity(len, len * 8);
    let mut b = StringBuilder::with_capacity(len, len * 8);
    py.detach(|| -> PyResult<()> {
        for_each_str(&data, |_, v| match v {
            Some(s) => {
                let (x, y) = f(s);
                a.append_value(x);
                b.append_value(y);
            }
            None => {
                a.append_null();
                b.append_null();
            }
        })
    })?;
    Ok((a.finish().into_data(), b.finish().into_data()))
}

/// Apply `f` over each non-null string element, producing a QUAD of outputs
/// (e.g. `split_address` -> street + city + state + zip). A null input emits a
/// null in ALL FOUR output arrays. On a present row `f` returns
/// `(String, Option, Option, Option)`: the first output is always a value; the
/// other three may each be null even on a present row (the split may match only
/// the street) -- matching `address.py::split_address`.
#[allow(clippy::type_complexity)]
pub fn map_str_to_str_quad<F>(
    py: Python,
    data: ArrayData,
    f: F,
) -> PyResult<(ArrayData, ArrayData, ArrayData, ArrayData)>
where
    F: Fn(&str) -> (String, Option<String>, Option<String>, Option<String>) + Sync,
{
    let len = make_array(data.clone()).len();
    let mut a = StringBuilder::with_capacity(len, len * 8);
    let mut b = StringBuilder::with_capacity(len, len * 8);
    let mut c = StringBuilder::with_capacity(len, len * 4);
    let mut d = StringBuilder::with_capacity(len, len * 6);
    py.detach(|| -> PyResult<()> {
        for_each_str(&data, |_, v| match v {
            Some(s) => {
                let (w, x, y, z) = f(s);
                a.append_value(w);
                b.append_option(x);
                c.append_option(y);
                d.append_option(z);
            }
            None => {
                a.append_null();
                b.append_null();
                c.append_null();
                d.append_null();
            }
        })
    })?;
    Ok((
        a.finish().into_data(),
        b.finish().into_data(),
        c.finish().into_data(),
        d.finish().into_data(),
    ))
}

/// Apply `f` over two string arrays element-wise, producing one output (e.g.
/// `merge_name(first, last) -> full`). `f` sees each side's null-ness (it may
/// combine one present + one null side), and returns `None` to emit a null.
/// Values are collected first (arrays may be StringArray or LargeStringArray),
/// then combined with the GIL released.
pub fn zip_str_to_str<F>(py: Python, first: ArrayData, last: ArrayData, f: F) -> PyResult<ArrayData>
where
    F: Fn(Option<&str>, Option<&str>) -> Option<String> + Sync,
{
    let mut av: Vec<Option<String>> = Vec::new();
    for_each_str(&first, |_, v| av.push(v.map(|s| s.to_string())))?;
    let mut bv: Vec<Option<String>> = Vec::new();
    for_each_str(&last, |_, v| bv.push(v.map(|s| s.to_string())))?;
    let len = av.len();
    let mut builder = StringBuilder::with_capacity(len, len * 12);
    py.detach(|| {
        for (i, a_opt) in av.iter().enumerate() {
            let a = a_opt.as_deref();
            let b = bv.get(i).and_then(|o| o.as_deref());
            match f(a, b) {
                Some(out) => builder.append_value(out),
                None => builder.append_null(),
            }
        }
    });
    Ok(builder.finish().into_data())
}

pub fn map_str_to_i64<F>(py: Python, data: ArrayData, f: F) -> PyResult<ArrayData>
where
    F: Fn(&str) -> Option<i64> + Sync,
{
    let len = make_array(data.clone()).len();
    let mut builder = Int64Builder::with_capacity(len);
    py.detach(|| -> PyResult<()> {
        for_each_str(&data, |_, v| match v.and_then(&f) {
            Some(out) => builder.append_value(out),
            None => builder.append_null(),
        })
    })?;
    Ok(builder.finish().into_data())
}

pub fn map_str_to_bool<F>(py: Python, data: ArrayData, f: F) -> PyResult<ArrayData>
where
    F: Fn(&str) -> Option<bool> + Sync,
{
    let len = make_array(data.clone()).len();
    let mut builder = BooleanBuilder::with_capacity(len);
    py.detach(|| -> PyResult<()> {
        for_each_str(&data, |_, v| match v.and_then(&f) {
            Some(out) => builder.append_value(out),
            None => builder.append_null(),
        })
    })?;
    Ok(builder.finish().into_data())
}

pub fn map_str_to_f64<F>(py: Python, data: ArrayData, f: F) -> PyResult<ArrayData>
where
    F: Fn(&str) -> Option<f64> + Sync,
{
    let len = make_array(data.clone()).len();
    let mut builder = Float64Builder::with_capacity(len);
    py.detach(|| -> PyResult<()> {
        for_each_str(&data, |_, v| match v.and_then(&f) {
            Some(out) => builder.append_value(out),
            None => builder.append_null(),
        })
    })?;
    Ok(builder.finish().into_data())
}

/// Apply `f` over each element of a Float64 array, receiving `None` for a
/// null slot and `Some(x)` otherwise; `f` returns `None` to emit a null,
/// `Some(out)` to emit a value. Unlike the string mappers, `f` sees the
/// null-ness itself (some numeric ops, like `fill_zero`, act ON nulls rather
/// than passing them through untouched).
pub fn map_f64_to_f64<F>(py: Python, data: ArrayData, f: F) -> PyResult<ArrayData>
where
    F: Fn(Option<f64>) -> Option<f64> + Sync,
{
    let arr = make_array(data.clone());
    let a = arr
        .as_any()
        .downcast_ref::<Float64Array>()
        .ok_or_else(|| PyTypeError::new_err("expected an Arrow Float64 array"))?;
    let len = a.len();
    let mut builder = Float64Builder::with_capacity(len);
    py.detach(|| {
        for i in 0..len {
            let v = if a.is_null(i) { None } else { Some(a.value(i)) };
            match f(v) {
                Some(out) => builder.append_value(out),
                None => builder.append_null(),
            }
        }
    });
    Ok(builder.finish().into_data())
}
