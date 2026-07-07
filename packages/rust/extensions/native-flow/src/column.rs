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

use arrow::array::{make_array, Array, ArrayRef, LargeStringArray, StringArray};
use arrow::compute::{cast, concat};
use arrow::datatypes::{DataType, Field, Schema};
use arrow::ffi_stream::{ArrowArrayStreamReader, FFI_ArrowArrayStream};
use arrow::record_batch::{RecordBatch, RecordBatchIterator, RecordBatchReader};
use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyCapsule, PyList};

use goldenflow_core::chain::{apply_chain, Kernel};

const STREAM_NAME: &[u8] = b"arrow_array_stream";

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
        } else {
            return Err(PyTypeError::new_err(
                "to_pylist requires a Utf8/LargeUtf8 column",
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
        let mut kernels = Vec::with_capacity(ops.len());
        for (name, params) in &ops {
            let refs: Vec<&str> = params.iter().map(String::as_str).collect();
            kernels.push(Kernel::from_op(name, &refs).ok_or_else(|| {
                PyValueError::new_err(format!("not a fusable chain kernel: {name}"))
            })?);
        }
        let (array, changed) = py.detach(|| -> PyResult<(ArrayRef, Vec<u64>)> {
            if let Some(s) = self.array.as_any().downcast_ref::<StringArray>() {
                let r = apply_chain(s, &kernels);
                Ok((Arc::new(r.array) as ArrayRef, r.changed))
            } else if let Some(s) = self.array.as_any().downcast_ref::<LargeStringArray>() {
                let r = apply_chain(s, &kernels);
                Ok((Arc::new(r.array) as ArrayRef, r.changed))
            } else {
                Err(PyTypeError::new_err(
                    "Column.apply_chain requires a Utf8/LargeUtf8 column",
                ))
            }
        })?;
        Ok((Column::new(array), changed))
    }
}
