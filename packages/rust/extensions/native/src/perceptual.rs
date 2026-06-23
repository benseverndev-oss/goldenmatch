//! PyO3 shims over `goldenmatch-perceptual-core` (multimodal-ER crawl tier, ADR 0022).
//!
//! Thin wrappers: validate, delegate to the pyo3-free core, return plain ints.
//! The core is byte-identical with the Python reference (`core/perceptual.py`) —
//! the `perceptual_golden.json` fixture is the shared parity oracle. The Python
//! caller selects these only when `native_enabled("perceptual")`.
use goldenmatch_perceptual_core::{fingerprint_audio, phash_image, radial_variance};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

fn validate_grid(grid: &[Vec<f64>]) -> PyResult<()> {
    if grid.is_empty() || grid[0].is_empty() {
        return Err(PyValueError::new_err("luma grid must be non-empty"));
    }
    let width = grid[0].len();
    if grid.iter().any(|r| r.len() != width) {
        return Err(PyValueError::new_err(
            "luma grid rows must all have the same length",
        ));
    }
    Ok(())
}

/// 64-bit DCT perceptual hash of one decoded luma grid.
#[pyfunction]
pub fn perceptual_phash_image(grid: Vec<Vec<f64>>) -> PyResult<u64> {
    validate_grid(&grid)?;
    Ok(phash_image(&grid))
}

/// Per-image 64-bit pHash for a batch of decoded luma grids (the column path).
#[pyfunction]
pub fn perceptual_phash_batch(grids: Vec<Vec<Vec<f64>>>) -> PyResult<Vec<u64>> {
    grids
        .iter()
        .map(|g| {
            validate_grid(g)?;
            Ok(phash_image(g))
        })
        .collect()
}

/// Haitsma-Kalker-style robust audio fingerprint of decoded mono PCM.
#[pyfunction]
pub fn perceptual_fingerprint_audio(samples: Vec<f64>, sample_rate: u32) -> PyResult<Vec<u32>> {
    if sample_rate == 0 {
        return Err(PyValueError::new_err("sample_rate must be positive"));
    }
    Ok(fingerprint_audio(&samples, sample_rate))
}

/// Rotation/crop-aware radial-variance profile of one decoded luma grid (ADR 0022
/// finding 1). Returns the per-angle variance vector; the comparison
/// (`radial_align_similarity`) stays in Python.
#[pyfunction]
pub fn perceptual_radial_variance(grid: Vec<Vec<f64>>) -> PyResult<Vec<f64>> {
    validate_grid(&grid)?;
    Ok(radial_variance(&grid))
}
