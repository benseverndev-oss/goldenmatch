// goldenmatch_embed(text: Utf8) -> FixedSizeList<Float32, dim>. Loads a saved
// GoldenEmbedModel from GOLDENEMBED_MODEL_DIR once at construction and runs the
// pure-Rust featurize + ONNX projection per batch — a zero-CPython SQL embed
// path. NULL Utf8 -> empty string -> the model's zero-ish vector (matches the
// Python None convention used by the string-scorer UDFs).
//
// `ort` 2.x `Session::run` takes `&mut self` and `GoldenEmbed::embed` is `&mut`,
// so the loaded model is wrapped in `Arc<Mutex<…>>`: correctness under
// DataFusion's parallel batch execution over throughput. The Python caller
// already parallelizes across queries, and embed batches are coarse, so the
// per-batch lock is not the bottleneck.
use std::any::Any;
use std::sync::{Arc, Mutex};

use arrow_array::builder::{FixedSizeListBuilder, Float32Builder};
use arrow_array::cast::AsArray;
use arrow_schema::{DataType, Field};
use datafusion_common::error::{DataFusionError, Result as DataFusionResult};
use datafusion_expr::{
    ColumnarValue, ScalarFunctionArgs, ScalarUDF, ScalarUDFImpl, Signature, Volatility,
};
use datafusion_ffi::udf::FFI_ScalarUDF;
use goldenembed::GoldenEmbed;
use pyo3::types::PyCapsule;
use pyo3::{pyclass, pymethods, Bound, PyResult, Python};

#[pyclass(
    from_py_object,
    name = "EmbedUDF",
    module = "goldenmatch_datafusion_udf",
    subclass
)]
#[derive(Clone)]
pub(crate) struct EmbedUDF {
    signature: Signature,
    dim: i32,
    model: Arc<Mutex<GoldenEmbed>>,
}

// ScalarUDFImpl requires Eq + Hash + PartialEq. The loaded model isn't
// meaningfully comparable, so identity is the (single) output dim — there is
// only ever one goldenmatch_embed UDF per session.
impl PartialEq for EmbedUDF {
    fn eq(&self, other: &Self) -> bool {
        self.dim == other.dim
    }
}
impl Eq for EmbedUDF {}
impl std::hash::Hash for EmbedUDF {
    fn hash<H: std::hash::Hasher>(&self, state: &mut H) {
        self.dim.hash(state);
    }
}
impl std::fmt::Debug for EmbedUDF {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "EmbedUDF{{dim:{}}}", self.dim)
    }
}

#[pymethods]
impl EmbedUDF {
    #[new]
    fn new() -> PyResult<Self> {
        let dir = std::env::var("GOLDENEMBED_MODEL_DIR").map_err(|_| {
            pyo3::exceptions::PyRuntimeError::new_err(
                "GOLDENEMBED_MODEL_DIR not set (a saved GoldenEmbedModel directory)",
            )
        })?;
        let model = GoldenEmbed::load(&dir)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        let dim = model.dim() as i32;
        Ok(Self {
            signature: Signature::exact(vec![DataType::Utf8], Volatility::Immutable),
            dim,
            model: Arc::new(Mutex::new(model)),
        })
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

impl ScalarUDFImpl for EmbedUDF {
    fn as_any(&self) -> &dyn Any {
        self
    }

    fn name(&self) -> &str {
        "goldenmatch_embed"
    }

    fn signature(&self) -> &Signature {
        &self.signature
    }

    fn return_type(&self, _arg_types: &[DataType]) -> DataFusionResult<DataType> {
        Ok(DataType::FixedSizeList(
            Arc::new(Field::new("item", DataType::Float32, false)),
            self.dim,
        ))
    }

    fn invoke_with_args(
        &self,
        args: ScalarFunctionArgs,
    ) -> DataFusionResult<ColumnarValue> {
        let arrs = ColumnarValue::values_to_arrays(&args.args)?;
        let texts = arrs[0].as_string::<i32>();
        // NULL -> "" (matches the native string-scorer NULL convention).
        let owned: Vec<&str> = texts.iter().map(|o| o.unwrap_or("")).collect();
        let vecs = {
            let mut model = self.model.lock().map_err(|_| {
                DataFusionError::Execution("goldenmatch_embed model lock poisoned".into())
            })?;
            model
                .embed(&owned)
                .map_err(|e| DataFusionError::Execution(e.to_string()))?
        };
        let mut builder = FixedSizeListBuilder::new(Float32Builder::new(), self.dim);
        for row in vecs {
            builder.values().append_slice(&row);
            builder.append(true);
        }
        Ok(ColumnarValue::Array(Arc::new(builder.finish())))
    }
}
