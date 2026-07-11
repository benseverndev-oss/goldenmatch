//! pyarrow<->Arrow marshalling shims for the combinatorial key /
//! functional-dependency kernels in `goldencheck-core`.
//!
//! Interning (Arrow value -> dense `u64` id, nulls to a reserved id, including
//! Dictionary-encoded/Categorical columns) now lives in
//! `goldencheck_core::arrow_support::intern_column`; these `#[pyfunction]`s
//! only decode pyarrow arrays into `ArrayRef` and call the core Arrow API.
use arrow::array::{make_array, ArrayData, ArrayRef};
use arrow::pyarrow::PyArrowType;
use pyo3::prelude::*;

fn to_arrays(v: Vec<PyArrowType<ArrayData>>) -> Vec<ArrayRef> {
    v.into_iter().map(|a| make_array(a.0)).collect()
}

fn map_err(e: arrow::error::ArrowError) -> PyErr {
    pyo3::exceptions::PyTypeError::new_err(e.to_string())
}

/// Search for minimal composite keys over `field_arrays`.
///
/// `single_unique[c]` marks columns already unique on their own (the caller
/// detects these cheaply and reports them as simple keys); subsets touching
/// them are skipped so results are genuinely composite. Returns each key as a
/// sorted list of column indices into `field_arrays`. Delegates to
/// `goldencheck_core::composite_key_search`.
#[pyfunction]
#[pyo3(signature = (field_arrays, max_size, single_unique))]
pub fn composite_key_search(
    field_arrays: Vec<PyArrowType<ArrayData>>,
    max_size: usize,
    single_unique: Vec<bool>,
) -> PyResult<Vec<Vec<usize>>> {
    goldencheck_core::composite_key_search(&to_arrays(field_arrays), max_size, &single_unique)
        .map_err(map_err)
}

/// Whether `lhs -> rhs` holds (every distinct lhs value maps to one rhs value).
/// Delegates to `goldencheck_core::functional_dependency_holds`.
#[pyfunction]
pub fn functional_dependency_holds(
    lhs: PyArrowType<ArrayData>,
    rhs: PyArrowType<ArrayData>,
) -> PyResult<bool> {
    goldencheck_core::functional_dependency_holds(
        make_array(lhs.0).as_ref(),
        make_array(rhs.0).as_ref(),
    )
    .map_err(map_err)
}

/// Discover all strict single-column FDs `(det_idx, dep_idx)` among
/// `field_arrays` (delegates to
/// `goldencheck_core::discover_functional_dependencies`).
#[pyfunction]
pub fn discover_functional_dependencies(
    field_arrays: Vec<PyArrowType<ArrayData>>,
) -> PyResult<Vec<(usize, usize)>> {
    goldencheck_core::discover_functional_dependencies(&to_arrays(field_arrays)).map_err(map_err)
}

/// Discover *approximate* FDs `(det_idx, dep_idx, n_violations)` holding for a
/// fraction of rows in `[min_confidence, 1.0)` (delegates to
/// `goldencheck_core::discover_approximate_fds`).
#[pyfunction]
#[pyo3(signature = (field_arrays, min_confidence))]
pub fn discover_approximate_fds(
    field_arrays: Vec<PyArrowType<ArrayData>>,
    min_confidence: f64,
) -> PyResult<Vec<(usize, usize, usize)>> {
    goldencheck_core::discover_approximate_fds(&to_arrays(field_arrays), min_confidence)
        .map_err(map_err)
}

/// Row indices where `dep` deviates from its per-`det`-group mode (the rows that
/// break an otherwise-strong dependency). Delegates to
/// `goldencheck_core::fd_violation_rows`.
#[pyfunction]
pub fn fd_violation_rows(
    det: PyArrowType<ArrayData>,
    dep: PyArrowType<ArrayData>,
) -> PyResult<Vec<usize>> {
    goldencheck_core::fd_violation_rows(make_array(det.0).as_ref(), make_array(dep.0).as_ref())
        .map_err(map_err)
}
