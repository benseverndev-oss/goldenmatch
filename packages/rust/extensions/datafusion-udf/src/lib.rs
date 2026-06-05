// goldenmatch_datafusion_udf — the datafusion-ffi extension module. Registers
// the native string scorers (jaro_winkler / token_sort / levenshtein) as FFI
// ScalarUDFs so a Python `datafusion` SessionContext can import them and
// register them via the PyCapsule FFI bridge. Each delegates per-pair scoring
// to the shared `goldenmatch-score-core` crate, the SAME source of truth the
// `goldenmatch._native` per-pair scorers use. Module-name matches the [lib] name
// + maturin module-name so PyInit_goldenmatch_datafusion_udf is the init symbol.

use pyo3::prelude::*;

use crate::embed_udf::EmbedUDF;
use crate::scalar_udf::{JaroWinklerUDF, LevenshteinUDF, TokenSortUDF};

pub(crate) mod embed_udf;
pub(crate) mod scalar_udf;

#[pymodule]
fn goldenmatch_datafusion_udf(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<JaroWinklerUDF>()?;
    m.add_class::<TokenSortUDF>()?;
    m.add_class::<LevenshteinUDF>()?;
    m.add_class::<EmbedUDF>()?;
    Ok(())
}
