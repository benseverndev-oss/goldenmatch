//! Arrow shims over goldenflow_core::text. Bytes in, kernel per element, bytes
//! out; GIL released. All logic lives in the core.
//!
//! The trivial-text family (`strip`/`lowercase`/`uppercase` + `collapse_whitespace`
//! / `normalize_*` / `remove_*` / `pad_*`) takes the Arrow-COLUMNAR apply path
//! (write into one shared buffer / whole-buffer ASCII case-fold) -- measured 4-10x
//! over the per-element path, byte-identical (goldenflow-core `columnar` +
//! `benches/columnar_pilot.rs`). Each routes its `*_into` streaming kernel through
//! `map_str_columnar`; the `String`-returning kernel is the LargeUtf8 fallback.
//! `normalize_unicode` is included (its char loop streams cleanly). The rest stay
//! on the scalar `map_str_to_str`: `title_case`/`fix_mojibake` are compute-bound
//! (allocation isn't the cost), and `truncate`/`extract_numbers` don't stream into
//! a single append buffer.
use crate::util::{ascii_case_columnar, map_str_columnar, map_str_to_str};
use arrow::array::ArrayData;
use arrow::pyarrow::PyArrowType;
use goldenflow_core::text;
use pyo3::prelude::*;

#[pyfunction]
pub fn strip_arrow(py: Python, array: PyArrowType<ArrayData>) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_columnar(
        py,
        array.0,
        |s, buf| buf.push_str(text::strip(s)),
        |s| text::strip(s).to_string(),
    )?))
}

#[pyfunction]
pub fn collapse_whitespace_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_columnar(
        py,
        array.0,
        text::collapse_whitespace_into,
        text::collapse_whitespace,
    )?))
}

#[pyfunction]
pub fn normalize_quotes_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_columnar(
        py,
        array.0,
        text::normalize_quotes_into,
        text::normalize_quotes,
    )?))
}

#[pyfunction]
pub fn normalize_line_endings_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_columnar(
        py,
        array.0,
        text::normalize_line_endings_into,
        text::normalize_line_endings,
    )?))
}

#[pyfunction]
pub fn remove_html_tags_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_columnar(
        py,
        array.0,
        text::remove_html_tags_into,
        text::remove_html_tags,
    )?))
}

#[pyfunction]
pub fn remove_urls_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_columnar(
        py,
        array.0,
        text::remove_urls_into,
        text::remove_urls,
    )?))
}

#[pyfunction]
pub fn remove_digits_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_columnar(
        py,
        array.0,
        text::remove_digits_into,
        text::remove_digits,
    )?))
}

#[pyfunction]
pub fn remove_punctuation_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_columnar(
        py,
        array.0,
        text::remove_punctuation_into,
        text::remove_punctuation,
    )?))
}

#[pyfunction]
pub fn remove_emojis_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_columnar(
        py,
        array.0,
        text::remove_emojis_into,
        text::remove_emojis,
    )?))
}

#[pyfunction]
pub fn extract_numbers_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(text::extract_numbers(s))
    })?))
}

/// Truncate to the first `n` characters. `n` defaults to 255; a negative `n`
/// clamps to 0.
#[pyfunction]
#[pyo3(signature = (array, n=255))]
pub fn truncate_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
    n: i64,
) -> PyResult<PyArrowType<ArrayData>> {
    let nn = if n < 0 { 0usize } else { n as usize };
    Ok(PyArrowType(map_str_to_str(py, array.0, move |s| {
        Some(text::truncate(s, nn))
    })?))
}

/// Left-pad to `width` characters with `pad` (default width 10, pad `'0'`).
#[pyfunction]
#[pyo3(signature = (array, width=10, pad='0'))]
pub fn pad_left_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
    width: i64,
    pad: char,
) -> PyResult<PyArrowType<ArrayData>> {
    let w = if width < 0 { 0usize } else { width as usize };
    Ok(PyArrowType(map_str_columnar(
        py,
        array.0,
        move |s, buf| text::pad_left_into(s, w, pad, buf),
        move |s| text::pad_left(s, w, pad),
    )?))
}

/// Right-pad to `width` characters with `pad` (default width 10, pad `' '`).
#[pyfunction]
#[pyo3(signature = (array, width=10, pad=' '))]
pub fn pad_right_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
    width: i64,
    pad: char,
) -> PyResult<PyArrowType<ArrayData>> {
    let w = if width < 0 { 0usize } else { width as usize };
    Ok(PyArrowType(map_str_columnar(
        py,
        array.0,
        move |s, buf| text::pad_right_into(s, w, pad, buf),
        move |s| text::pad_right(s, w, pad),
    )?))
}

#[pyfunction]
pub fn lowercase_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(ascii_case_columnar(
        py,
        array.0,
        false,
        text::lowercase,
    )?))
}

#[pyfunction]
pub fn uppercase_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(ascii_case_columnar(
        py,
        array.0,
        true,
        text::uppercase,
    )?))
}

#[pyfunction]
pub fn title_case_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(text::title_case(s))
    })?))
}

#[pyfunction]
pub fn normalize_unicode_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_columnar(
        py,
        array.0,
        text::normalize_unicode_into,
        text::normalize_unicode,
    )?))
}

#[pyfunction]
pub fn fix_mojibake_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(text::fix_mojibake(s))
    })?))
}
