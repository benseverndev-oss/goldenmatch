// AddOneUDF — a trivial Int64 -> Int64 scalar UDF used as the datafusion-ffi
// feasibility gate. Mirrors the IsNullUDF in apache/datafusion-python @ 53.0.0
// examples/datafusion-ffi-example/src/scalar_udf.rs verbatim for every FFI-API
// detail (trait-method signatures, the cr"datafusion_scalar_udf" capsule name,
// the FFI_ScalarUDF::from(Arc::new(ScalarUDF::from(self.clone()))) chain);
// only the body (Any->Bool null check) is swapped for Int64 + 1.

use std::any::Any;
use std::sync::Arc;

use arrow_array::{Array, Int64Array};
use arrow_schema::DataType;
use datafusion_common::ScalarValue;
use datafusion_common::error::Result as DataFusionResult;
use datafusion_expr::{
    ColumnarValue, ScalarFunctionArgs, ScalarUDF, ScalarUDFImpl, Signature, Volatility,
};
use datafusion_ffi::udf::FFI_ScalarUDF;
use pyo3::types::PyCapsule;
use pyo3::{Bound, PyResult, Python, pyclass, pymethods};

#[pyclass(
    from_py_object,
    name = "AddOneUDF",
    module = "goldenmatch_datafusion_udf",
    subclass
)]
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub(crate) struct AddOneUDF {
    signature: Signature,
}

#[pymethods]
impl AddOneUDF {
    #[new]
    fn new() -> Self {
        Self {
            signature: Signature::exact(vec![DataType::Int64], Volatility::Immutable),
        }
    }

    fn __datafusion_scalar_udf__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyCapsule>> {
        let name = cr"datafusion_scalar_udf".into();

        let func = Arc::new(ScalarUDF::from(self.clone()));
        let provider = FFI_ScalarUDF::from(func);

        PyCapsule::new(py, provider, Some(name))
    }
}

impl ScalarUDFImpl for AddOneUDF {
    fn as_any(&self) -> &dyn Any {
        self
    }

    fn name(&self) -> &str {
        "add_one"
    }

    fn signature(&self) -> &Signature {
        &self.signature
    }

    fn return_type(&self, _arg_types: &[DataType]) -> DataFusionResult<DataType> {
        Ok(DataType::Int64)
    }

    fn invoke_with_args(&self, args: ScalarFunctionArgs) -> DataFusionResult<ColumnarValue> {
        let input = &args.args[0];

        Ok(match input {
            ColumnarValue::Array(arr) => {
                let arr = arr
                    .as_any()
                    .downcast_ref::<Int64Array>()
                    .expect("add_one expects an Int64 array");
                let out: Int64Array = arr.iter().map(|v| v.map(|x| x + 1)).collect();
                ColumnarValue::Array(Arc::new(out))
            }
            ColumnarValue::Scalar(sv) => match sv {
                ScalarValue::Int64(Some(x)) => {
                    ColumnarValue::Scalar(ScalarValue::Int64(Some(x + 1)))
                }
                _ => ColumnarValue::Scalar(ScalarValue::Int64(None)),
            },
        })
    }
}
