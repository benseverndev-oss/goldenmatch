// Layer 1 — Planner types (A1 scaffold).
// Function implementations land in A2 (auto_chunk_size) and A3 (decide_plan).
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
