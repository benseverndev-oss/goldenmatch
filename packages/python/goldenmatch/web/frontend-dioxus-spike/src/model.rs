//! Ported straight from `web/frontend/src/lib/api.ts` — the exact wire shape
//! the FastAPI `/api/v1/sensitivity` endpoint returns. This is the "lib layer"
//! step of the migration: `types.ts` -> serde structs. Deserializes the same
//! JSON the React app consumes today, unchanged.

use serde::Deserialize;

#[derive(Debug, Clone, Deserialize)]
pub struct SensitivityPoint {
    pub value: f64,
    pub cluster_count_a: i64,
    pub cluster_count_b: i64,
    pub unchanged: i64,
    pub merged: i64,
    pub partitioned: i64,
    pub overlapping: i64,
    pub twi: f64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct StabilityPoint {
    pub value: f64,
    pub unchanged: i64,
    pub merged: i64,
    pub partitioned: i64,
    pub overlapping: i64,
    pub twi: f64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Stability {
    pub best_value: f64,
    pub best_unchanged_pct: f64,
    pub points: Vec<StabilityPoint>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct SensitivityResponse {
    pub field: String,
    pub baseline_value: Option<f64>,
    pub sample_n: i64,
    pub stability: Stability,
    pub points: Vec<SensitivityPoint>,
}
