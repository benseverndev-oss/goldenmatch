//! Arrow shims over goldenflow_core::text. Bytes in, kernel per element, bytes
//! out; GIL released. All logic lives in the core.
use crate::util::map_str_to_str;
use arrow::array::ArrayData;
use arrow::pyarrow::PyArrowType;
use goldenflow_core::text;
use pyo3::prelude::*;

#[pyfunction]
pub fn strip_arrow(py: Python, array: PyArrowType<ArrayData>) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(text::strip(s).to_string())
    })?))
}

#[pyfunction]
pub fn collapse_whitespace_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(text::collapse_whitespace(s))
    })?))
}

#[pyfunction]
pub fn normalize_quotes_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(text::normalize_quotes(s))
    })?))
}

#[pyfunction]
pub fn normalize_line_endings_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(text::normalize_line_endings(s))
    })?))
}

#[pyfunction]
pub fn remove_html_tags_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(text::remove_html_tags(s))
    })?))
}

#[pyfunction]
pub fn remove_urls_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(text::remove_urls(s))
    })?))
}

#[pyfunction]
pub fn remove_digits_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(text::remove_digits(s))
    })?))
}

#[pyfunction]
pub fn remove_punctuation_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(text::remove_punctuation(s))
    })?))
}

#[pyfunction]
pub fn remove_emojis_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(text::remove_emojis(s))
    })?))
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
    Ok(PyArrowType(map_str_to_str(py, array.0, move |s| {
        Some(text::pad_left(s, w, pad))
    })?))
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
    Ok(PyArrowType(map_str_to_str(py, array.0, move |s| {
        Some(text::pad_right(s, w, pad))
    })?))
}

#[pyfunction]
pub fn lowercase_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(text::lowercase(s))
    })?))
}

#[pyfunction]
pub fn uppercase_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
) -> PyResult<PyArrowType<ArrayData>> {
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(text::uppercase(s))
    })?))
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
    Ok(PyArrowType(map_str_to_str(py, array.0, |s| {
        Some(text::normalize_unicode(s))
    })?))
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
