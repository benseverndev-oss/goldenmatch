// goldenmatch_datafusion_udf — the datafusion-ffi feasibility-gate extension
// module. Registers AddOneUDF so a Python `datafusion` SessionContext can
// import it and register it via the PyCapsule FFI bridge. Module-name matches
// the [lib] name + maturin module-name so PyInit_goldenmatch_datafusion_udf is
// the init symbol.

use pyo3::prelude::*;

use crate::scalar_udf::AddOneUDF;

pub(crate) mod scalar_udf;

#[pymodule]
fn goldenmatch_datafusion_udf(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<AddOneUDF>()?;
    Ok(())
}
