//! Deterministic auto-config decisions shared across surfaces (no pyo3).
//! Port oracle: docs/superpowers/specs/2026-06-20-autoconfig-native-core-design.md
pub mod classify;
pub mod extrapolate;
pub mod planner;
pub mod thresholds;
#[cfg(feature = "arrow")]
pub mod profile;

// Layer 1 re-exports (A3)
pub use planner::{
    auto_chunk_size, decide_plan, BackendName, Capabilities, ClusteringStrategy,
    ExecutionPlan, PlannerInput, RuntimeProfile, SpillThreshold,
};
// Layer 2 re-exports (B4)
pub use classify::{classify_columns, ColType, ColumnProfile, ColumnStats};
// S1 extrapolation kernel re-exports
pub use extrapolate::{extrapolate_pair_count, ExtrapolationInput, ExtrapolationOutput};
// S2b/S3 threshold kernel re-exports
pub use thresholds::sparse_match_floor;
