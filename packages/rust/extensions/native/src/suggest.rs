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
/// - `scored_pairs`    – RecordBatch with columns `id_a:i64, id_b:i64, score:f64`
/// - `clusters`        – RecordBatch with columns `cluster_id:i64, size:i64, confidence:f64, quality:utf8, oversized:bool`
/// - `column_signals`  – RecordBatch with columns `field:utf8, col_type:utf8, scorer:utf8, in_blocking:bool, in_negative_evidence:bool, identity_score:f64, corruption_score:f64, collision_rate:f64, cardinality_ratio:f64, null_rate:f64, variant_rate:f64`
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
