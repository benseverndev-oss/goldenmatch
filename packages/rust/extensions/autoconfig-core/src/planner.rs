//! Layer 1 — Planner: types, constants, and `auto_chunk_size`.
//! `decide_plan` (the 8-rule table) lands in A3.
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum BackendName {
    PolarsDirect,
    Chunked,
    Duckdb,
    Ray,
    Bucket,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ClusteringStrategy {
    InMemory,
    PartitionedUnionFind,
    StreamingCc,
}

// NO `None` variant: Python pair_spill_threshold is `Literal[...] | None`; 5 of 8
// rules leave it None -> JSON null. Model absence as Option (below), NOT a variant
// (a `None` variant under snake_case serializes "none" and breaks oracle parity).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SpillThreshold {
    Ram,
    Duckdb,
    DiskPerWorker,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct RuntimeProfile {
    pub available_ram_gb: f64,
    pub cpu_count: u32,
    pub disk_free_gb: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Capabilities {
    pub bucket_available: bool,
    pub ray_available: bool,
    pub ray_auto_select: bool,
    pub user_backend: Option<BackendName>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PlannerInput {
    pub n_rows_full: u64,
    pub estimated_pair_count: u64,
    pub runtime: RuntimeProfile,
    pub caps: Capabilities,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ExecutionPlan {
    pub backend: BackendName,
    pub chunk_size: Option<u64>,
    pub max_workers: u32,
    pub pair_spill_threshold: Option<SpillThreshold>,
    pub clustering_strategy: ClusteringStrategy,
    pub rule_name: String,
}

// ── Named threshold constants (port from autoconfig_planner_rules.py) ────────
// Rule 1
// (no constant; predicate is n_rows <= 1)

// Rule 2: simple plan
pub const SIMPLE_PLAN_MAX_ROWS: u64 = 100_000;
pub const SIMPLE_PLAN_MAX_PAIRS: u64 = 50_000_000;

// Rule 3: fast-box plan
pub const FAST_BOX_MIN_RAM_GB: f64 = 32.0;

// Rule 3b: bucket-suggested band
pub const BUCKET_SUGGESTED_MAX_ROWS: u64 = 750_000;
pub const PAIR_SCORE_BYTES: u64 = 64;
pub const BUCKET_RAM_SAFETY_FRACTION: f64 = 0.5;

// Rule 4: chunked plan
pub const CHUNKED_MAX_PAIRS: u64 = 5_000_000_000;
pub const CHUNKED_MIN_RAM_GB: f64 = 16.0;
pub const CHUNKED_TARGET_RAM_USE_FRACTION: f64 = 0.6;
pub const CHUNKED_BYTES_PER_ROW: u64 = 1024;

// Rule 6: Ray
pub const RAY_MIN_ROWS: u64 = 50_000_000;

// Rule 5: DuckDB
pub const DUCKDB_MIN_PAIRS: u64 = 5_000_000_000;
pub const DUCKDB_MAX_RAM_GB: f64 = 16.0;
pub const DUCKDB_MAX_WORKERS: u32 = 8;

/// Port of `auto_chunk_size` from `autoconfig_planner_rules.py:218-232`.
///
/// Target ~60 % of available RAM per chunk; clamp result to [10_000, 1_000_000].
pub fn auto_chunk_size(n_rows_full: u64, available_ram_gb: f64) -> u64 {
    let estimated_gb = (n_rows_full * CHUNKED_BYTES_PER_ROW) as f64 / (1024_f64.powi(3));
    let denom = (available_ram_gb * CHUNKED_TARGET_RAM_USE_FRACTION).max(0.001);
    let target_chunks = (estimated_gb / denom).ceil().max(1.0) as u64;
    let chunk = n_rows_full / target_chunks;
    chunk.clamp(10_000, 1_000_000)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chunk_size_clamps() {
        // 10M rows, 64 GB → estimated_gb = 10M*1024 / 1024^3 ≈ 9.537
        // denom = 64*0.6 = 38.4 → target_chunks = ceil(9.537/38.4) = 1 → chunk = 10_000_000
        // clamp to 1_000_000
        assert_eq!(auto_chunk_size(10_000_000, 64.0), 1_000_000);
        // 5_000 rows, 64 GB → estimated_gb tiny → target_chunks=1 → chunk=5000 < 10_000
        // clamp to 10_000
        assert_eq!(auto_chunk_size(5_000, 64.0), 10_000);
    }
}
