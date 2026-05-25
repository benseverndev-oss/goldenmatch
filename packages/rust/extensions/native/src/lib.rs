//! `goldenmatch._native` — native acceleration kernels (PyO3 extension module).
//!
//! Phase 1 (this module): clustering kernels mirroring `core/cluster.py`. Each
//! function is a behavior-exact replacement for a pure-Python hot loop; the
//! Python side selects it only when `GOLDENMATCH_NATIVE` opts in (default stays
//! Python until the parity + DQbench gates pass). Spec:
//! `packages/python/goldenmatch/docs/design/2026-05-25-rust-acceleration-spec.md`.
use pyo3::prelude::*;

mod cluster;
mod score;

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add_function(wrap_pyfunction!(cluster::connected_components, m)?)?;
    m.add_function(wrap_pyfunction!(cluster::severe_bridge_count, m)?)?;
    m.add_function(wrap_pyfunction!(cluster::cluster_confidence, m)?)?;
    m.add_function(wrap_pyfunction!(score::jaro_winkler_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(score::levenshtein_similarity, m)?)?;
    m.add_function(wrap_pyfunction!(score::token_sort_ratio, m)?)?;
    m.add_function(wrap_pyfunction!(score::score_block_pairs, m)?)?;
    Ok(())
}
