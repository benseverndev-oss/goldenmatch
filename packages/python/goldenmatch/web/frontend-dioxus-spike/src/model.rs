//! Ported straight from `web/frontend/src/lib/api.ts` — the exact wire shape
//! the FastAPI `/api/v1/sensitivity` endpoint returns. This is the "lib layer"
//! step of the migration: `types.ts` -> serde structs. Deserializes the same
//! JSON the React app consumes today, unchanged.

use std::collections::BTreeMap;

use serde::Deserialize;

#[derive(Debug, Clone, PartialEq, Deserialize)]
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

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct StabilityPoint {
    pub value: f64,
    pub unchanged: i64,
    pub merged: i64,
    pub partitioned: i64,
    pub overlapping: i64,
    pub twi: f64,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct Stability {
    pub best_value: f64,
    pub best_unchanged_pct: f64,
    pub points: Vec<StabilityPoint>,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct SensitivityResponse {
    pub field: String,
    pub baseline_value: Option<f64>,
    pub sample_n: i64,
    pub stability: Stability,
    pub points: Vec<SensitivityPoint>,
}


// ── Identity graph (the knowledge-graph view) ───────────────────────────────
// Ported from the `/api/v1/identities/{entity_id}` response shape
// (`goldenmatch/identity/query.py::IdentityView.to_dict`). ONE resolved
// identity = an entity node + its source records + the evidence edges between
// those records. Optional fields carry `#[serde(default)]` so a partial or
// older payload still deserializes.

/// A source record that belongs to an entity (a graph node).
#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct IdentityRecord {
    pub record_id: String,
    #[serde(default)]
    pub source: Option<String>,
    #[serde(default)]
    pub source_pk: Option<String>,
    /// The raw record values, shown in the node tooltip / used to label the node.
    #[serde(default)]
    pub payload: BTreeMap<String, serde_json::Value>,
}

/// An evidence edge between two records (a graph link). `kind` is one of
/// `same_as` / `possible_same_as` / `conflicts_with`.
#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct EvidenceEdge {
    pub record_a_id: String,
    pub record_b_id: String,
    pub kind: String,
    #[serde(default)]
    pub score: Option<f64>,
    #[serde(default)]
    pub matchkey_name: Option<String>,
    #[serde(default)]
    pub run_name: Option<String>,
}

/// The aggregated read of one identity — the exact `/identities/{id}` shape.
#[derive(Debug, Clone, PartialEq, Deserialize)]
pub struct IdentityView {
    pub entity_id: String,
    #[serde(default)]
    pub status: Option<String>,
    #[serde(default)]
    pub confidence: Option<f64>,
    #[serde(default)]
    pub dataset: Option<String>,
    #[serde(default)]
    pub records: Vec<IdentityRecord>,
    #[serde(default)]
    pub edges: Vec<EvidenceEdge>,
}
