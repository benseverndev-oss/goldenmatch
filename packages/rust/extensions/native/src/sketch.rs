//! PyO3 shims over `goldenmatch-sketch-core` (MinHash/LSH, #1081).
//!
//! Thin wrappers: parse the mode string, delegate to the pyo3-free core, return
//! plain `list[list[int]]`. The core is byte-identical with the Python reference
//! (`core/sketch.py`) and the TS port — the `sketch_golden.json` fixture is the
//! shared parity oracle. The Python caller (`core/sketch.py`) selects these only
//! when `native_enabled("sketch")`.
use goldenmatch_sketch_core::{
    band_hashes_batch, signature_batch, simhash_band_hashes_batch, ShingleMode,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

fn parse_mode(mode: &str) -> PyResult<ShingleMode> {
    ShingleMode::parse(mode)
        .ok_or_else(|| PyValueError::new_err(format!("unknown shingle mode: {mode:?}")))
}

/// Per-record banded-LSH bucket hashes for a batch of texts.
#[pyfunction]
pub fn sketch_band_hashes_batch(
    texts: Vec<String>,
    mode: &str,
    k: usize,
    num_perms: usize,
    num_bands: usize,
    seed: u64,
) -> PyResult<Vec<Vec<u64>>> {
    if k < 1 {
        return Err(PyValueError::new_err(format!(
            "shingle k must be >= 1, got {k}"
        )));
    }
    if num_bands == 0 || !num_perms.is_multiple_of(num_bands) {
        return Err(PyValueError::new_err(format!(
            "num_perms {num_perms} not divisible by num_bands {num_bands}"
        )));
    }
    let mode = parse_mode(mode)?;
    Ok(band_hashes_batch(
        &texts, mode, k, num_perms, num_bands, seed,
    ))
}

/// Per-record SimHash banded-LSH bucket hashes for a batch of dense f64 vectors.
///
/// SimHash buckets DENSE embedding-style vectors (one bit per random hyperplane),
/// complementing `sketch_band_hashes_batch`'s bucketing of SPARSE shingle sets.
/// The projection matrix is built once per `(seed, dim, num_planes)` and reused.
#[pyfunction]
pub fn sketch_simhash_band_hashes_batch(
    vectors: Vec<Vec<f64>>,
    num_planes: usize,
    num_bands: usize,
    seed: u64,
) -> PyResult<Vec<Vec<u64>>> {
    if num_bands == 0 || !num_planes.is_multiple_of(num_bands) {
        return Err(PyValueError::new_err(format!(
            "num_planes {num_planes} not divisible by num_bands {num_bands}"
        )));
    }
    Ok(simhash_band_hashes_batch(
        &vectors, num_planes, num_bands, seed,
    ))
}

/// Per-record MinHash signatures for a batch of texts.
#[pyfunction]
pub fn sketch_signature_batch(
    texts: Vec<String>,
    mode: &str,
    k: usize,
    num_perms: usize,
    seed: u64,
) -> PyResult<Vec<Vec<u64>>> {
    if k < 1 {
        return Err(PyValueError::new_err(format!(
            "shingle k must be >= 1, got {k}"
        )));
    }
    let mode = parse_mode(mode)?;
    Ok(signature_batch(&texts, mode, k, num_perms, seed))
}
