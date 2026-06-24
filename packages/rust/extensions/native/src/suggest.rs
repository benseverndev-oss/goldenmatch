//! Thin PyO3 shim exposing `goldenmatch_suggest_core::suggest` to Python.
//!
//! Receives pyarrow RecordBatches via the Arrow C Data Interface (same pattern
//! as `score.rs::score_block_pairs_arrow`) and delegates entirely to the
//! pyo3-free `goldenmatch-suggest-core` crate. No logic lives here.
use arrow::pyarrow::PyArrowType;
use arrow::record_batch::RecordBatch;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// Suggest config adjustments from a finished run's Arrow artifacts.
///
/// Parameters mirror `goldenmatch_suggest_core::suggest`:
/// - `scored_pairs`    – RecordBatch with columns `id_a`, `id_b`, `score`
/// - `clusters`        – RecordBatch with columns `record_id`, `cluster_id`
/// - `column_signals`  – RecordBatch with per-column diagnostic signals
/// - `config_json`     – current domain config serialized as JSON
/// - `priors_json`     – learned priors (empty object `"{}"` if none)
///
/// Returns a JSON string `[{suggestion}, ...]` (ranked list of
/// `ConfigSuggestion` objects). Raises `ValueError` on any kernel error.
#[pyfunction]
pub fn suggest_config(
    scored_pairs: PyArrowType<RecordBatch>,
    clusters: PyArrowType<RecordBatch>,
    column_signals: PyArrowType<RecordBatch>,
    config_json: &str,
    priors_json: &str,
) -> PyResult<String> {
    goldenmatch_suggest_core::suggest(
        &scored_pairs.0,
        &clusters.0,
        &column_signals.0,
        config_json,
        priors_json,
    )
    .map_err(PyValueError::new_err)
}
