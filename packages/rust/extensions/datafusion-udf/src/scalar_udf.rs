// Native string scorers exposed as datafusion-ffi ScalarUDFs over
// (Utf8, Utf8) -> Float64. Each UDF delegates per-pair scoring to the
// pyo3-free `goldenmatch-score-core` crate — the SAME source of truth the
// `goldenmatch._native` per-pair scorers use — so the FFI path scores
// byte-identically to native by construction (parity asserted end-to-end in
// tests/test_datafusion_ffi_udf.py).
//
// Every FFI-API detail (trait-method signatures, the cr"datafusion_scalar_udf"
// capsule name, the FFI_ScalarUDF::from(Arc::new(ScalarUDF::from(self.clone())))
// chain) mirrors the Stage-A AddOneUDF, which in turn mirrors the IsNullUDF in
// apache/datafusion-python @ 53.0.0 examples/datafusion-ffi-example verbatim.
//
// NULL CONVENTION: a NULL Utf8 input is scored as the empty string `""` (not
// null-propagated), matching the Python `string_similarity` gate in
// goldenmatch/native.py (`"" if a is None else str(a)`). So every output row is
// a non-null Float64.
//
// SCALE: jaro_winkler / levenshtein return [0, 1]. token_sort here returns
// [0, 1] (score-core's `score_one`-style fuzz::ratio scale), i.e. the native
// `token_sort_ratio` 0-100 value divided by 100 — the test accounts for the
// /100 between the two surfaces.

use std::any::Any;
use std::sync::Arc;

use arrow_array::cast::AsArray;
use arrow_array::Float64Array;
use arrow_schema::DataType;
use datafusion_common::error::Result as DataFusionResult;
use datafusion_expr::{
    ColumnarValue, ScalarFunctionArgs, ScalarUDF, ScalarUDFImpl, Signature, Volatility,
};
use datafusion_ffi::udf::FFI_ScalarUDF;
use pyo3::types::PyCapsule;
use pyo3::{Bound, PyResult, Python, pyclass, pymethods};

/// Build a `Float64Array` by scoring each (a, b) pair from two Utf8 columns,
/// mapping NULL -> "" before scoring (see NULL CONVENTION above). `args` are
/// collapsed Scalar-or-Array values; `values_to_arrays` expands any Scalar to an
/// Array of `number_rows` so the two-arg zip is always well-formed.
fn invoke_pair_scorer(
    args: ScalarFunctionArgs,
    score: fn(&str, &str) -> f64,
) -> DataFusionResult<ColumnarValue> {
    let arrs = ColumnarValue::values_to_arrays(&args.args)?;
    let a = arrs[0].as_string::<i32>();
    let b = arrs[1].as_string::<i32>();
    let out: Float64Array = a
        .iter()
        .zip(b.iter())
        .map(|(oa, ob)| Some(score(oa.unwrap_or(""), ob.unwrap_or(""))))
        .collect();
    Ok(ColumnarValue::Array(Arc::new(out)))
}

macro_rules! string_scorer_udf {
    ($udf:ident, $py_name:literal, $sql_name:literal, $score:path) => {
        #[pyclass(
            from_py_object,
            name = $py_name,
            module = "goldenmatch_datafusion_udf",
            subclass
        )]
        #[derive(Debug, Clone, PartialEq, Eq, Hash)]
        pub(crate) struct $udf {
            signature: Signature,
        }

        #[pymethods]
        impl $udf {
            #[new]
            fn new() -> Self {
                Self {
                    signature: Signature::exact(
                        vec![DataType::Utf8, DataType::Utf8],
                        Volatility::Immutable,
                    ),
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

        impl ScalarUDFImpl for $udf {
            fn as_any(&self) -> &dyn Any {
                self
            }

            fn name(&self) -> &str {
                $sql_name
            }

            fn signature(&self) -> &Signature {
                &self.signature
            }

            fn return_type(&self, _arg_types: &[DataType]) -> DataFusionResult<DataType> {
                Ok(DataType::Float64)
            }

            fn invoke_with_args(
                &self,
                args: ScalarFunctionArgs,
            ) -> DataFusionResult<ColumnarValue> {
                invoke_pair_scorer(args, $score)
            }
        }
    };
}

string_scorer_udf!(
    JaroWinklerUDF,
    "JaroWinklerUDF",
    "jaro_winkler",
    goldenmatch_score_core::jaro_winkler_similarity
);

// token_sort here is the [0, 1] form (native token_sort_ratio / 100). We call
// score-core's `score_one` with id=2 (the UNSCALED fuzz::ratio) rather than
// `token_sort_ratio` (which is *100), so the FFI scale is [0, 1]. The test
// asserts this equals native.token_sort_ratio(a, b) / 100.0.
fn token_sort_unit(a: &str, b: &str) -> f64 {
    goldenmatch_score_core::score_one(2, a, b)
}

string_scorer_udf!(TokenSortUDF, "TokenSortUDF", "token_sort", token_sort_unit);

string_scorer_udf!(
    LevenshteinUDF,
    "LevenshteinUDF",
    "levenshtein",
    goldenmatch_score_core::levenshtein_similarity
);
