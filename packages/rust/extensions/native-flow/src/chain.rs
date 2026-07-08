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
use goldenflow_core::chain::{
    apply_chain, apply_chain_f64, apply_chain_nullable, apply_chain_str, apply_chain_str_nullable,
    Kernel, NullableKernel, NumericKernel,
};
use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;

/// A resolved run of chain ops: either ALL total string kernels (the zero-alloc
/// fast path) or a run that contains at least one `Option`-returning
/// URL/company/email kernel (the nullable path, into which total kernels fold as
/// `Total`). The shared resolver so the list binding, the `Column`, and the CSV
/// path all route the same way.
pub enum ChainOps {
    Total(Vec<Kernel>),
    Nullable(Vec<NullableKernel>),
}

/// Resolve `ops` to a [`ChainOps`]: prefer the all-total fast path; fall back to
/// the nullable chain if any op is `Option`-returning. Errors only if a name is
/// not fusable at all.
pub fn resolve_chain(ops: &[(String, Vec<String>)]) -> PyResult<ChainOps> {
    let mut totals = Vec::with_capacity(ops.len());
    let mut all_total = true;
    for (name, params) in ops {
        let refs: Vec<&str> = params.iter().map(String::as_str).collect();
        match Kernel::from_op(name, &refs) {
            Some(k) => totals.push(k),
            None => {
                all_total = false;
                break;
            }
        }
    }
    if all_total {
        return Ok(ChainOps::Total(totals));
    }
    let mut nks = Vec::with_capacity(ops.len());
    for (name, params) in ops {
        let refs: Vec<&str> = params.iter().map(String::as_str).collect();
        nks.push(
            NullableKernel::from_op(name, &refs).ok_or_else(|| {
                PyValueError::new_err(format!("not a fusable chain kernel: {name}"))
            })?,
        );
    }
    Ok(ChainOps::Nullable(nks))
}

/// Capability probe: this build's `apply_chain_str_list` / `Column.apply_chain` /
/// `transform_csv` AUTO-ROUTE `Option`-returning (URL/company/email) kernels
/// (native-flow 0.20+). The host gates accepting nullable columnar configs on this
/// symbol's presence — a pre-0.20 wheel lacks it, so those configs stay on the
/// Polars engine (no hard error on a skewed wheel).
#[pyfunction]
pub fn chain_supports_nullable() -> bool {
    true
}

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

/// The fusable NULLABLE (`Option<String>`) kernel names — the URL / company /
/// email `Option`-returning families — for the host's nullable coverage guard.
#[pyfunction]
pub fn fusable_nullable_kernel_names() -> Vec<String> {
    NullableKernel::NULLABLE_NAMES
        .iter()
        .map(|s| s.to_string())
        .collect()
}

/// Apply a run of nullable string kernels (`(name, params)` tuples) over a
/// Utf8 / LargeUtf8 array in one pass. A run may MIX total kernels (strip,
/// lowercase, …) with the `Option`-returning URL/company/email ones; a value a
/// kernel can't parse becomes a NULL cell that passes through the rest of the
/// run. Returns `(transformed_array, changed)` with the per-kernel affected
/// counts (a non-null→null row is not counted, matching the per-transform path).
#[pyfunction]
pub fn apply_chain_nullable_arrow(
    py: Python,
    array: PyArrowType<ArrayData>,
    ops: Vec<(String, Vec<String>)>,
) -> PyResult<(PyArrowType<ArrayData>, Vec<u64>)> {
    let mut kernels = Vec::with_capacity(ops.len());
    for (name, params) in &ops {
        let refs: Vec<&str> = params.iter().map(String::as_str).collect();
        kernels.push(
            NullableKernel::from_op(name, &refs).ok_or_else(|| {
                PyValueError::new_err(format!("not a fusable chain kernel: {name}"))
            })?,
        );
    }
    let arr = make_array(array.0);
    if let Some(s) = arr.as_any().downcast_ref::<StringArray>() {
        let (out, changed) = py.detach(|| {
            let r = apply_chain_nullable(s, &kernels);
            (r.array.into_data(), r.changed)
        });
        Ok((PyArrowType(out), changed))
    } else if let Some(s) = arr.as_any().downcast_ref::<LargeStringArray>() {
        let (out, changed) = py.detach(|| {
            let r = apply_chain_nullable(s, &kernels);
            (r.array.into_data(), r.changed)
        });
        Ok((PyArrowType(out), changed))
    } else {
        Err(PyTypeError::new_err(
            "fused nullable apply requires a Utf8 or LargeUtf8 array",
        ))
    }
}

/// Arrow-FREE fused string chain over a Python list — the columnar-engine path
/// that needs no pyarrow, no Polars, no Arrow. Each op is a `(name, params)` tuple
/// (owned no-arg + parameterized string kernels; `Kernel::from_op`). Nulls (`None`)
/// pass through unchanged and are NOT counted; non-null values are threaded through
/// the chain (`goldenflow_core::chain::apply_chain_str`). Returns
/// `(values, per-kernel changed counts)` — the list analogue of `apply_chain_ops_arrow`.
#[pyfunction]
pub fn apply_chain_str_list(
    values: Vec<Option<String>>,
    ops: Vec<(String, Vec<String>)>,
) -> PyResult<(Vec<Option<String>>, Vec<u64>)> {
    match resolve_chain(&ops)? {
        ChainOps::Total(kernels) => {
            // Thread only the non-null values (total kernels never null); nulls pass.
            let non_null: Vec<&str> = values.iter().filter_map(|v| v.as_deref()).collect();
            let (transformed, changed) = apply_chain_str(&non_null, &kernels);
            // Scatter the transformed values back into their (non-null) positions.
            let mut it = transformed.into_iter();
            let out: Vec<Option<String>> = values
                .iter()
                .map(|v| {
                    v.as_ref()
                        .map(|_| it.next().expect("aligned with non-null count"))
                })
                .collect();
            Ok((out, changed))
        }
        ChainOps::Nullable(kernels) => {
            // A kernel may turn non-null -> null mid-run, so thread Options directly.
            let opt_refs: Vec<Option<&str>> = values.iter().map(|v| v.as_deref()).collect();
            let (out, changed) = apply_chain_str_nullable(&opt_refs, &kernels);
            Ok((out, changed))
        }
    }
}
