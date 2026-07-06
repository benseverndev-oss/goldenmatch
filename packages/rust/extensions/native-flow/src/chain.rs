//! Fused columnar apply shim — run a WHOLE owned-kernel chain over one column in
//! a single Arrow round-trip (`goldenflow_core::chain::apply_chain`), instead of
//! the host crossing the boundary once per transform. Utf8 and LargeUtf8 are both
//! handled (Polars exports strings as LargeUtf8, so the i64 arm is the one that
//! fires on real data). Returns the transformed array plus the per-kernel
//! affected-row counts so the host can emit a byte-identical per-transform audit.

use arrow::array::{make_array, Array, ArrayData, LargeStringArray, StringArray};
use arrow::pyarrow::PyArrowType;
use goldenflow_core::chain::{apply_chain, Kernel};
use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;

/// The fusable kernel names the compiled chain supports, for the host's
/// coverage guard (asserts Python `FUSABLE_KERNELS` == this set).
#[pyfunction]
pub fn fusable_kernel_names() -> Vec<String> {
    Kernel::ALL_NAMES.iter().map(|s| s.to_string()).collect()
}

/// Apply `kernel_names` (registry transform names, e.g. `["strip","lowercase"]`)
/// in order over `array`. Every name must be a fusable chain kernel
/// (`Kernel::from_name`); an unknown name is an error (the host only sends names
/// it resolved against the same table). Returns `(transformed_array, changed)`
/// where `changed[i]` is the number of non-null rows the i-th kernel altered.
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
    let data = array.0;
    let arr = make_array(data.clone());
    if let Some(s) = arr.as_any().downcast_ref::<StringArray>() {
        let (out, changed) = py.detach(|| {
            let r = apply_chain(s, &kernels);
            (r.array.into_data(), r.changed)
        });
        Ok((PyArrowType(out), changed))
    } else if let Some(s) = arr.as_any().downcast_ref::<LargeStringArray>() {
        let (out, changed) = py.detach(|| {
            let r = apply_chain(s, &kernels);
            (r.array.into_data(), r.changed)
        });
        Ok((PyArrowType(out), changed))
    } else {
        Err(PyTypeError::new_err(
            "apply_chain_arrow requires a Utf8 or LargeUtf8 array",
        ))
    }
}
