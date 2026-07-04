// Canonical record fingerprint exposed as a datafusion-ffi ScalarUDF over
// Utf8 -> Utf8, delegating to the pyo3-free `goldenmatch-fingerprint-core` crate
// — the SAME source of truth the Postgres `goldenmatch_record_fingerprint`, the
// DuckDB UDF, the edge-TS wasm, and the Python native path use. So the 64-hex
// SHA-256 record id is byte-identical across every surface by construction
// (parity asserted in tests/test_datafusion_ffi_udf.py against the shared
// `fingerprint-core/golden/fingerprint_golden.json` oracle).
//
// fingerprint-core is a pure hashing crate (sha2 + serde_json, no arrow), so —
// unlike graph-core/goldenembed — it introduces no arrow-version constraint on
// this crate's arrow-58 stack.
//
// NULL / error convention: a NULL input yields NULL (propagated); an input that
// is not a fingerprintable JSON object (invalid JSON, a non-object, a nested
// array/object value, or a non-finite float) also yields NULL — the graceful
// SQL choice (Postgres `error!`s; this surface, like DuckDB's fail-soft, returns
// NULL). Valid records hash identically on every surface.
//
// The FFI boilerplate (trait-method signatures, the cr"datafusion_scalar_udf"
// capsule name, the FFI_ScalarUDF::from(Arc::new(ScalarUDF::from(self.clone())))
// chain) mirrors `scalar_udf.rs` / the canonical datafusion-ffi-example verbatim.

use std::any::Any;
use std::sync::Arc;

use arrow_array::cast::AsArray;
use arrow_array::StringArray;
use arrow_schema::DataType;
use datafusion_common::error::Result as DataFusionResult;
use datafusion_expr::{
    ColumnarValue, ScalarFunctionArgs, ScalarUDF, ScalarUDFImpl, Signature, Volatility,
};
use datafusion_ffi::udf::FFI_ScalarUDF;
use pyo3::types::PyCapsule;
use pyo3::{Bound, PyResult, Python, pyclass, pymethods};

#[pyclass(
    from_py_object,
    name = "FingerprintUDF",
    module = "goldenmatch_datafusion_udf",
    subclass
)]
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub(crate) struct FingerprintUDF {
    signature: Signature,
}

#[pymethods]
impl FingerprintUDF {
    #[new]
    fn new() -> Self {
        Self {
            signature: Signature::exact(vec![DataType::Utf8], Volatility::Immutable),
        }
    }

    fn __datafusion_scalar_udf__<'py>(
        &self,
        py: Python<'py>,
    ) -> PyResult<Bound<'py, PyCapsule>> {
        let name = cr"datafusion_scalar_udf".into();
        let func = Arc::new(ScalarUDF::from(self.clone()));
        let provider = FFI_ScalarUDF::from(func);
        PyCapsule::new(py, provider, Some(name))
    }
}

impl ScalarUDFImpl for FingerprintUDF {
    fn as_any(&self) -> &dyn Any {
        self
    }

    fn name(&self) -> &str {
        "goldenmatch_record_fingerprint"
    }

    fn signature(&self) -> &Signature {
        &self.signature
    }

    fn return_type(&self, _arg_types: &[DataType]) -> DataFusionResult<DataType> {
        Ok(DataType::Utf8)
    }

    fn invoke_with_args(
        &self,
        args: ScalarFunctionArgs,
    ) -> DataFusionResult<ColumnarValue> {
        let arrs = ColumnarValue::values_to_arrays(&args.args)?;
        let a = arrs[0].as_string::<i32>();
        // NULL in -> NULL; un-fingerprintable input (Err) -> NULL; else the hex.
        let out: StringArray = a
            .iter()
            .map(|oa| oa.and_then(|s| goldenmatch_fingerprint_core::fingerprint_json(s).ok()))
            .collect();
        Ok(ColumnarValue::Array(Arc::new(out)))
    }
}
