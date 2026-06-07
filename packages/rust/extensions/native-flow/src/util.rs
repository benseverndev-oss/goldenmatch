//! Arrow read/build helpers shared by the kernels.
//!
//! Input string arrays are read zero-copy (`value(i)` borrows the Arrow buffer);
//! outputs are built into new Arrow arrays and handed back across the C Data
//! Interface. Each mapper releases the GIL around the compute loop.

use arrow::array::{
    Array, ArrayData, BooleanBuilder, Int64Builder, LargeStringArray, StringArray, StringBuilder,
    make_array,
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

pub fn map_str_to_str<F>(py: Python, data: ArrayData, f: F) -> PyResult<ArrayData>
where
    F: Fn(&str) -> Option<String> + Sync,
{
    let len = make_array(data.clone()).len();
    let mut builder = StringBuilder::with_capacity(len, len * 12);
    py.allow_threads(|| -> PyResult<()> {
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

pub fn map_str_to_i64<F>(py: Python, data: ArrayData, f: F) -> PyResult<ArrayData>
where
    F: Fn(&str) -> Option<i64> + Sync,
{
    let len = make_array(data.clone()).len();
    let mut builder = Int64Builder::with_capacity(len);
    py.allow_threads(|| -> PyResult<()> {
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
    py.allow_threads(|| -> PyResult<()> {
        for_each_str(&data, |_, v| match v.and_then(&f) {
            Some(out) => builder.append_value(out),
            None => builder.append_null(),
        })
    })?;
    Ok(builder.finish().into_data())
}
