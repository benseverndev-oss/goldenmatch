//! Native Arrow `Column` — Phase 1b of the Polars eviction. A Rust-owned Arrow
//! array that ingests from / egresses to Python via the Arrow **C-Data /
//! PyCapsule** interface (`__arrow_c_stream__`), so it needs **no pyarrow and no
//! Polars** to hold or move Arrow buffers. Transforms run on it via the owned
//! fused chain (Column -> Column, zero-copy), avoiding the per-transform
//! `to_arrow`/`from_arrow` round-trip that couples the engine to Polars.
//!
//! Correctness is proven by parity (round-trip a Polars Series through `Column`);
//! the speed win is a CI number (the dev box is too noisy to trust locally).

use std::ffi::CString;
use std::sync::Arc;

use arrow::array::{
    make_array, Array, ArrayRef, BooleanArray, Float64Array, Int64Array, LargeStringArray,
    StringArray,
};
use arrow::compute::{cast, concat};
use arrow::datatypes::{DataType, Field, Schema};
use arrow::ffi_stream::{ArrowArrayStreamReader, FFI_ArrowArrayStream};
use arrow::record_batch::{RecordBatch, RecordBatchIterator, RecordBatchReader};
use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyCapsule, PyDict, PyList};

use goldenflow_core::chain::{apply_chain, apply_chain_nullable};
use goldenflow_core::float_fmt::float_to_polars_string;
use goldenflow_core::profile::{profile_column, TypeHint};

use crate::chain::{resolve_chain, ChainOps};
use crate::csvio::OpRecord;
use crate::numeric_columnar::{resolve_numeric, run_numeric_column};
use crate::split_columnar::{resolve_split, run_split_column};

const STREAM_NAME: &[u8] = b"arrow_array_stream";

/// `apply_split` result: `(source_column, [(output_name, output_column)], records)`.
type SplitCols = (Column, Vec<(String, Column)>, Vec<OpRecord>);

/// A Rust-owned Arrow column (one logical column, always a single contiguous
/// array after ingest). Utf8 or LargeUtf8 for the owned string transform path.
#[pyclass]
pub struct Column {
    array: ArrayRef,
}

impl Column {
    fn new(array: ArrayRef) -> Self {
        Column { array }
    }
}

#[pymethods]
impl Column {
    /// Ingest a Python object exposing the Arrow C-stream interface (Polars
    /// Series/DataFrame-column, or any `__arrow_c_stream__` producer) into a
    /// Rust-owned array — zero-copy, pyarrow-free. Chunks are concatenated into
    /// one contiguous array.
    #[staticmethod]
    fn from_arrow(py: Python, obj: &Bound<'_, PyAny>) -> PyResult<Column> {
        let capsule_obj = obj.call_method0("__arrow_c_stream__")?;
        let capsule = capsule_obj
            .cast::<PyCapsule>()
            .map_err(|_| PyTypeError::new_err("__arrow_c_stream__ did not return a PyCapsule"))?;
        // Take ownership of the FFI stream (per the Arrow PyCapsule protocol: move
        // it out, leaving a released/empty struct so the producer won't double-free).
        #[allow(deprecated)] // pointer() is fine here; the checked variant's API churned
        let ptr = capsule.pointer() as *mut FFI_ArrowArrayStream;
        if ptr.is_null() {
            return Err(PyValueError::new_err("null arrow_array_stream capsule"));
        }
        // Ingest is a one-time boundary op (not the hot loop), and the raw FFI
        // pointer isn't Send, so run it under the GIL. `from_raw` performs the
        // C-Data-interface move (the producer's release is transferred), so the
        // capsule won't double-free.
        let _ = py;
        let reader = unsafe { ArrowArrayStreamReader::from_raw(ptr) }
            .map_err(|e| PyValueError::new_err(format!("arrow stream import: {e}")))?;
        let empty_type = reader.schema().field(0).data_type().clone();
        let mut chunks: Vec<ArrayRef> = Vec::new();
        for batch in reader {
            let b = batch.map_err(|e| PyValueError::new_err(e.to_string()))?;
            if b.num_columns() != 1 {
                return Err(PyTypeError::new_err("expected a single-column stream"));
            }
            chunks.push(b.column(0).clone());
        }
        let array: ArrayRef = if chunks.len() == 1 {
            chunks.into_iter().next().unwrap()
        } else if chunks.is_empty() {
            make_array(arrow::array::ArrayData::new_empty(&empty_type))
        } else {
            let refs: Vec<&dyn Array> = chunks.iter().map(|a| a.as_ref()).collect();
            concat(&refs).map_err(|e| PyValueError::new_err(e.to_string()))?
        };
        // Polars 1.x exports strings as Utf8View; normalize to LargeUtf8 (what the
        // owned chain + egress expect). Same materialization the `to_arrow` path
        // already pays, but reached pyarrow-free.
        let array = if matches!(array.data_type(), DataType::Utf8View) {
            cast(&array, &DataType::LargeUtf8).map_err(|e| PyValueError::new_err(e.to_string()))?
        } else {
            array
        };
        Ok(Column::new(array))
    }

    fn __len__(&self) -> usize {
        self.array.len()
    }

    /// Build a `Column` from a Python `list[str | None]` — **Polars-free and
    /// pyarrow-free** ingest (no `__arrow_c_stream__` producer needed). The seam for
    /// the Polars-free in-memory execution path (Phase 4b): a `dict[str, list]` frame
    /// runs the owned chain / numeric / split kernels with Polars never imported.
    #[staticmethod]
    fn from_pylist(values: Vec<Option<String>>) -> Column {
        Column::new(Arc::new(LargeStringArray::from(values)))
    }

    /// Egress via the Arrow C-stream interface — the native Column IS an Arrow
    /// producer, so `pl.from_arrow(column)` / any Arrow consumer imports it
    /// zero-copy, pyarrow-free.
    #[pyo3(signature = (requested_schema=None))]
    fn __arrow_c_stream__<'py>(
        &self,
        py: Python<'py>,
        requested_schema: Option<Bound<'py, PyAny>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let _ = requested_schema; // we always export our own schema
        let field = Field::new("", self.array.data_type().clone(), true);
        let schema = Arc::new(Schema::new(vec![field]));
        let batch = RecordBatch::try_new(schema.clone(), vec![self.array.clone()])
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        let reader = RecordBatchIterator::new(vec![Ok(batch)], schema);
        let stream = FFI_ArrowArrayStream::new(Box::new(reader));
        let name = CString::new(STREAM_NAME).unwrap();
        let capsule = PyCapsule::new(py, stream, Some(name))?;
        Ok(capsule.into_any())
    }

    /// Materialize as a Python `list[str | None]` (Utf8/LargeUtf8 only). Egress
    /// helper for tests / the pure path; the zero-copy egress is
    /// `__arrow_c_stream__`.
    /// Materialize as a Python `list` — strings for a Utf8/LargeUtf8 column, and (so
    /// the Polars-free in-memory path can egress a numeric result) `int`/`float`/`None`
    /// for an Int64/Float64 column, matching what `pl.Series.to_list()` returns.
    fn to_pylist<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let list = PyList::empty(py);
        if let Some(a) = self.array.as_any().downcast_ref::<StringArray>() {
            for v in a.iter() {
                list.append(v)?;
            }
        } else if let Some(a) = self.array.as_any().downcast_ref::<LargeStringArray>() {
            for v in a.iter() {
                list.append(v)?;
            }
        } else if let Some(a) = self.array.as_any().downcast_ref::<Int64Array>() {
            for v in a.iter() {
                list.append(v)?;
            }
        } else if let Some(a) = self.array.as_any().downcast_ref::<Float64Array>() {
            for v in a.iter() {
                list.append(v)?;
            }
        } else {
            return Err(PyTypeError::new_err(
                "to_pylist requires a Utf8/LargeUtf8/Int64/Float64 column",
            ));
        }
        Ok(list)
    }

    /// Apply a run of owned string kernels (`(name, params)` tuples) to this
    /// column, returning `(new_column, per-kernel changed counts)`. Zero-copy
    /// Column->Column: no Polars, no per-transform Arrow round-trip.
    fn apply_chain(
        &self,
        py: Python,
        ops: Vec<(String, Vec<String>)>,
    ) -> PyResult<(Column, Vec<u64>)> {
        // Auto-route: an all-total run takes the zero-alloc fast chain; a run with
        // any Option-returning (URL/company/email) kernel takes the nullable chain
        // (total kernels fold in as Total). Byte-identical either way.
        let chain = resolve_chain(&ops)?;
        let err = || PyTypeError::new_err("Column.apply_chain requires a Utf8/LargeUtf8 column");
        let (array, changed) = py.detach(|| -> PyResult<(ArrayRef, Vec<u64>)> {
            match &chain {
                ChainOps::Total(ks) => {
                    if let Some(s) = self.array.as_any().downcast_ref::<StringArray>() {
                        let r = apply_chain(s, ks);
                        Ok((Arc::new(r.array) as ArrayRef, r.changed))
                    } else if let Some(s) = self.array.as_any().downcast_ref::<LargeStringArray>() {
                        let r = apply_chain(s, ks);
                        Ok((Arc::new(r.array) as ArrayRef, r.changed))
                    } else {
                        Err(err())
                    }
                }
                ChainOps::Nullable(ks) => {
                    if let Some(s) = self.array.as_any().downcast_ref::<StringArray>() {
                        let r = apply_chain_nullable(s, ks);
                        Ok((Arc::new(r.array) as ArrayRef, r.changed))
                    } else if let Some(s) = self.array.as_any().downcast_ref::<LargeStringArray>() {
                        let r = apply_chain_nullable(s, ks);
                        Ok((Arc::new(r.array) as ArrayRef, r.changed))
                    } else {
                        Err(err())
                    }
                }
            }
        })?;
        Ok((Column::new(array), changed))
    }

    /// In-memory numeric path (Phase 3 wave 3d): run a numeric config
    /// (`string* parser f64*`) over this string column and return a `Column` holding
    /// the RAW numeric result (Int64 / Float64) — egressed via `__arrow_c_stream__`
    /// as an Arrow column of that dtype, so the in-memory frame gets a real numeric
    /// column compared BY VALUE — plus the per-op manifest records (which still carry
    /// the formatted before/after samples). The caller casts the input to Utf8 first
    /// (Polars' numeric transforms cast to Utf8 internally, so this matches even for
    /// an already-numeric input column).
    fn apply_numeric(
        &self,
        py: Python,
        ops: Vec<(String, Vec<String>)>,
    ) -> PyResult<(Column, Vec<OpRecord>)> {
        let plan = resolve_numeric(&ops)
            .ok_or_else(|| PyValueError::new_err("not a numeric columnar config"))?;
        let arr = self
            .array
            .as_any()
            .downcast_ref::<LargeStringArray>()
            .ok_or_else(|| {
                PyTypeError::new_err("Column.apply_numeric requires a Utf8/LargeUtf8 column")
            })?;
        let (numcol, records) = py.detach(|| run_numeric_column(arr, &plan));
        Ok((Column::new(numcol.into_array()), records))
    }

    /// In-memory multi-output path (Phase 3 wave 3e): run a split config
    /// (`string* splitter`) over this string column, returning the source `Column`
    /// (unchanged by the split, only its string ops applied), the fixed-name output
    /// `Column`s to add to the frame, and the per-op manifest records. Each Column
    /// egresses via `__arrow_c_stream__`.
    fn apply_split(&self, py: Python, ops: Vec<(String, Vec<String>)>) -> PyResult<SplitCols> {
        let plan = resolve_split(&ops)
            .ok_or_else(|| PyValueError::new_err("not a split columnar config"))?;
        let arr = self
            .array
            .as_any()
            .downcast_ref::<LargeStringArray>()
            .ok_or_else(|| {
                PyTypeError::new_err("Column.apply_split requires a Utf8/LargeUtf8 column")
            })?;
        let (src, new_cols, records) = py.detach(|| run_split_column(arr, &plan));
        let new = new_cols
            .into_iter()
            .map(|(n, a)| (n, Column::new(Arc::new(a))))
            .collect();
        Ok((Column::new(Arc::new(src)), new, records))
    }

    /// Path 1 (columnar): profile this column in one native pass. Returns a dict
    /// `{null_count, unique_count, samples, inferred_type}`.
    ///
    /// The string path (Utf8/LargeUtf8/Utf8View) runs the owned
    /// `profile_column` over a borrowed `&str` view — null/unique/samples over
    /// the RAW values plus the type-inference decision. Typed columns
    /// (Int64/Float64/Boolean/Date/Timestamp) short-circuit the type via the
    /// hint and compute null/unique/samples off the typed buffer; `samples` are
    /// formatted to match Polars `cast(Utf8)`.
    fn profile<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        // Compute off-GIL (borrows only `self.array` + locals, like the sibling
        // `apply_chain`/`apply_numeric`/`apply_split` closures); build the PyDict
        // after detach returns the owned tuple.
        let (null_count, unique_count, samples, inferred_type) =
            py.detach(|| -> PyResult<(u64, u64, Vec<String>, String)> {
                Ok(match self.array.data_type() {
                    DataType::Utf8 | DataType::LargeUtf8 | DataType::Utf8View => {
                        // Cast to LargeUtf8 so profile_column sees a single string
                        // layout — a one-shot cost, unlike the per-transform chain
                        // path which dispatches on both String and LargeString.
                        let large = if matches!(self.array.data_type(), DataType::LargeUtf8) {
                            self.array.clone()
                        } else {
                            cast(&self.array, &DataType::LargeUtf8)
                                .map_err(|e| PyValueError::new_err(e.to_string()))?
                        };
                        let s = large
                            .as_any()
                            .downcast_ref::<LargeStringArray>()
                            .ok_or_else(|| PyTypeError::new_err("expected a LargeUtf8 column"))?;
                        let view: Vec<Option<&str>> = s.iter().collect();
                        let out = profile_column(&view, TypeHint::Utf8);
                        (
                            out.null_count,
                            out.unique_count,
                            out.samples,
                            out.inferred_type,
                        )
                    }
                    DataType::Int64 => {
                        let a = self.array.as_any().downcast_ref::<Int64Array>().unwrap();
                        let mut seen: std::collections::HashSet<i64> =
                            std::collections::HashSet::new();
                        let mut samples: Vec<String> = Vec::with_capacity(5);
                        for v in a.iter().flatten() {
                            seen.insert(v);
                            if samples.len() < 5 {
                                samples.push(v.to_string());
                            }
                        }
                        (
                            a.null_count() as u64,
                            seen.len() as u64,
                            samples,
                            "numeric".to_string(),
                        )
                    }
                    DataType::Float64 => {
                        let a = self.array.as_any().downcast_ref::<Float64Array>().unwrap();
                        // Fold -0.0/+0.0 and all NaN together to match Polars n_unique.
                        let mut seen: std::collections::HashSet<u64> =
                            std::collections::HashSet::new();
                        let mut samples: Vec<String> = Vec::with_capacity(5);
                        for v in a.iter().flatten() {
                            seen.insert(canon_f64_bits(v));
                            if samples.len() < 5 {
                                samples.push(float_to_polars_string(v));
                            }
                        }
                        (
                            a.null_count() as u64,
                            seen.len() as u64,
                            samples,
                            "numeric".to_string(),
                        )
                    }
                    DataType::Boolean => {
                        let a = self.array.as_any().downcast_ref::<BooleanArray>().unwrap();
                        let mut seen: std::collections::HashSet<bool> =
                            std::collections::HashSet::new();
                        let mut samples: Vec<String> = Vec::with_capacity(5);
                        for v in a.iter().flatten() {
                            seen.insert(v);
                            if samples.len() < 5 {
                                samples.push(if v { "true" } else { "false" }.to_string());
                            }
                        }
                        (
                            a.null_count() as u64,
                            seen.len() as u64,
                            samples,
                            "boolean".to_string(),
                        )
                    }
                    DataType::Date32 | DataType::Date64 | DataType::Timestamp(_, _) => {
                        // Temporal columns short-circuit to "date"; null/unique/samples
                        // come off a Utf8 cast (arrow's formatting — display-only, the
                        // inferred_type is fixed and the column-name override is applied
                        // by the Python caller).
                        let large = cast(&self.array, &DataType::LargeUtf8)
                            .map_err(|e| PyValueError::new_err(e.to_string()))?;
                        let s = large
                            .as_any()
                            .downcast_ref::<LargeStringArray>()
                            .ok_or_else(|| PyTypeError::new_err("temporal cast to Utf8 failed"))?;
                        let mut seen: std::collections::HashSet<&str> =
                            std::collections::HashSet::new();
                        let mut samples: Vec<String> = Vec::with_capacity(5);
                        for v in s.iter().flatten() {
                            seen.insert(v);
                            if samples.len() < 5 {
                                samples.push(v.to_string());
                            }
                        }
                        (
                            s.null_count() as u64,
                            seen.len() as u64,
                            samples,
                            "date".to_string(),
                        )
                    }
                    other => {
                        return Err(PyTypeError::new_err(format!(
                            "Column.profile does not support dtype {other:?}"
                        )));
                    }
                })
            })?;
        let dict = PyDict::new(py);
        dict.set_item("null_count", null_count)?;
        dict.set_item("unique_count", unique_count)?;
        dict.set_item("samples", samples)?;
        dict.set_item("inferred_type", inferred_type)?;
        Ok(dict)
    }
}

/// Fold `-0.0`/`+0.0` to one key and all NaN to one key so the float
/// `unique_count` matches Polars `n_unique`.
fn canon_f64_bits(x: f64) -> u64 {
    if x.is_nan() {
        0x7ff8_0000_0000_0000
    } else if x == 0.0 {
        0.0f64.to_bits()
    } else {
        x.to_bits()
    }
}
