//! `goldenmatch_embed._embed` — local ONNX embedder (PyO3 extension module).
//!
//! Thin pyo3 wrapper over the pyo3-free `goldenembed` crate so Python (the
//! DuckDB embed UDF) runs the SAME char-n-gram featurizer + ONNX projection as
//! the pgrx/DataFusion surfaces, by construction. All of `ort` (onnxruntime)
//! is confined to this wheel; `goldenembed` itself stays pyo3-free. Spec/issue:
//! #509 wave 2 (goldenembed-rs cutover).
use goldenembed::GoldenEmbed;
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use std::sync::Mutex;

/// Local ONNX embedder (goldenembed-rs) exposed to Python. Wraps a saved
/// in-house model dir; embed() runs the char-n-gram featurizer + ONNX
/// projection with zero CPython in the hot path.
///
/// `GoldenEmbed::embed` takes `&mut self` (the ort `Session` is mutated), so
/// the model lives behind a `Mutex`; `GoldenEmbed` is `Send`, so the pyclass is
/// safe to share across the GIL.
#[pyclass(name = "GoldenEmbed", module = "goldenmatch_embed._embed")]
pub struct PyGoldenEmbed {
    inner: Mutex<GoldenEmbed>,
    dim: usize,
}

#[pymethods]
impl PyGoldenEmbed {
    /// Load a saved model directory (config.json + model.onnx).
    #[staticmethod]
    fn load(dir: String) -> PyResult<Self> {
        let m = GoldenEmbed::load(&dir)
            .map_err(|e| PyRuntimeError::new_err(format!("goldenmatch-embed load: {e:#}")))?;
        let dim = m.dim();
        Ok(Self {
            inner: Mutex::new(m),
            dim,
        })
    }

    #[getter]
    fn dim(&self) -> usize {
        self.dim
    }

    /// Embed a list of texts -> list of float32 vectors (row-major).
    fn embed(&self, texts: Vec<String>) -> PyResult<Vec<Vec<f32>>> {
        let refs: Vec<&str> = texts.iter().map(String::as_str).collect();
        let mut guard = self
            .inner
            .lock()
            .map_err(|_| PyRuntimeError::new_err("goldenmatch-embed: model lock poisoned"))?;
        guard
            .embed(&refs)
            .map_err(|e| PyRuntimeError::new_err(format!("goldenmatch-embed embed: {e:#}")))
    }
}

#[pymodule]
fn _embed(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_class::<PyGoldenEmbed>()?;
    Ok(())
}
