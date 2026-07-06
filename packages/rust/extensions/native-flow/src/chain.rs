//! Fused columnar apply shim — run a WHOLE owned-kernel chain over one column in
//! a single Arrow round-trip (`goldenflow_core::chain::apply_chain`), instead of
//! the host crossing the boundary once per transform. Utf8 and LargeUtf8 are both
//! handled (Polars exports strings as LargeUtf8, so the i64 arm is the one that
//! fires on real data). Returns the transformed array plus the per-kernel
//! affected-row counts so the host can emit a byte-identical per-transform audit.
//!
//! Two entry points: [`apply_chain_arrow`] (no-arg names only, the original 0.12.0
//! symbol) and [`apply_chain_ops_arrow`] (superset — also the parameterized string
//! ops via `(name, params)` tuples). The host prefers the ops form when present and
//! falls back to the no-arg form on an older wheel.

use arrow::array::{make_array, Array, ArrayData, Float64Array, LargeStringArray, StringArray};
use arrow::pyarrow::PyArrowType;
use goldenflow_core::chain::{apply_chain, apply_chain_f64, Kernel, NumericKernel};
use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;

/// The fusable kernel names the compiled chain supports (no-arg + parameterized),
/// for the host's coverage guard (asserts Python `FUSABLE_KERNELS ∪
/// FUSABLE_PARAM_KERNELS` == this set).
#[pyfunction]
pub fn fusable_kernel_names() -> Vec<String> {
    Kernel::ALL_NAMES
        .iter()
        .chain(Kernel::PARAM_NAMES.iter())
        .map(|s| s.to_string())
        .collect()
}

/// Downcast `data` to Utf8 / LargeUtf8 and run `kernels` in one pass (GIL released),
/// returning the transformed array + per-kernel affected counts.
fn run_kernels(
    py: Python,
    data: ArrayData,
    kernels: &[Kernel],
) -> PyResult<(PyArrowType<ArrayData>, Vec<u64>)> {
    let arr = make_array(data);
    if let Some(s) = arr.as_any().downcast_ref::<StringArray>() {
        let (out, changed) = py.detach(|| {
            let r = apply_chain(s, kernels);
            (r.array.into_data(), r.changed)
        });
        Ok((PyArrowType(out), changed))
    } else if let Some(s) = arr.as_any().downcast_ref::<LargeStringArray>() {
        let (out, changed) = py.detach(|| {
            let r = apply_chain(s, kernels);
            (r.array.into_data(), r.changed)
        });
        Ok((PyArrowType(out), changed))
    } else {
        Err(PyTypeError::new_err(
            "fused apply requires a Utf8 or LargeUtf8 array",
        ))
    }
}

/// Apply `kernel_names` (no-arg registry names, e.g. `["strip","lowercase"]`) in
/// order over `array`. Every name must be a no-arg fusable kernel
/// (`Kernel::from_name`). Returns `(transformed_array, changed)` where `changed[i]`
/// is the number of non-null rows the i-th kernel altered.
#[pyfunction]
pub fn apply_chain_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
    kernel_names: Vec<String>,
) -> PyResult<(PyArrowType<ArrayData>, Vec<u64>)> {
    let mut kernels = Vec::with_capacity(kernel_names.len());
    for n in &kernel_names {
        kernels
            .push(Kernel::from_name(n).ok_or_else(|| {
                PyValueError::new_err(format!("not a fusable chain kernel: {n}"))
            })?);
    }
    run_kernels(py, array.0, &kernels)
}

/// Superset of [`apply_chain_arrow`]: each op is a `(name, params)` tuple, so the
/// parameterized string ops (`truncate` / `pad_left` / `pad_right`) fuse too
/// (`Kernel::from_op`; defaults/clamping match the per-transform arrow shims).
#[pyfunction]
pub fn apply_chain_ops_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
    ops: Vec<(String, Vec<String>)>,
) -> PyResult<(PyArrowType<ArrayData>, Vec<u64>)> {
    let mut kernels = Vec::with_capacity(ops.len());
    for (name, params) in &ops {
        let refs: Vec<&str> = params.iter().map(String::as_str).collect();
        kernels.push(
            Kernel::from_op(name, &refs).ok_or_else(|| {
                PyValueError::new_err(format!("not a fusable chain kernel: {name}"))
            })?,
        );
    }
    run_kernels(py, array.0, &kernels)
}

/// The fusable NUMERIC (f64) kernel names, for the host's f64 coverage guard
/// (asserts Python `FUSABLE_F64_KERNELS ∪ FUSABLE_F64_PARAM_KERNELS` == this set).
#[pyfunction]
pub fn fusable_f64_kernel_names() -> Vec<String> {
    NumericKernel::ALL_NAMES
        .iter()
        .map(|s| s.to_string())
        .collect()
}

/// Apply a run of owned f64->f64 kernels (`round`/`clamp`/`abs_value`/`fill_zero`,
/// each a `(name, params)` tuple) over a `Float64Array` in one pass. Returns
/// `(transformed_array, changed)` where `changed[i]` is the number of rows the
/// i-th kernel altered (matching the host's per-transform affected count). The
/// input must be a Float64 array (Polars f64 columns export as Float64).
#[pyfunction]
pub fn apply_chain_f64_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
    ops: Vec<(String, Vec<String>)>,
) -> PyResult<(PyArrowType<ArrayData>, Vec<u64>)> {
    let mut kernels = Vec::with_capacity(ops.len());
    for (name, params) in &ops {
        let refs: Vec<&str> = params.iter().map(String::as_str).collect();
        kernels.push(NumericKernel::from_op(name, &refs).ok_or_else(|| {
            PyValueError::new_err(format!("not a fusable f64 chain kernel: {name}"))
        })?);
    }
    let arr = make_array(array.0);
    let f = arr
        .as_any()
        .downcast_ref::<Float64Array>()
        .ok_or_else(|| PyTypeError::new_err("fused f64 apply requires a Float64 array"))?;
    let (out, changed) = py.detach(|| {
        let r = apply_chain_f64(f, &kernels);
        (r.array.into_data(), r.changed)
    });
    Ok((PyArrowType(out), changed))
}
