//! Deterministic auto-config decisions shared across surfaces (no pyo3).
//! Port oracle: docs/superpowers/specs/2026-06-20-autoconfig-native-core-design.md
pub mod classify;
pub mod planner;
#[cfg(feature = "arrow")]
pub mod profile;

// Layer 1 re-exports (A3)
pub use planner::{
    auto_chunk_size, decide_plan, BackendName, Capabilities, ClusteringStrategy,
    ExecutionPlan, PlannerInput, RuntimeProfile, SpillThreshold,
};
// classify_columns re-export is ADDED in Stage B (Layer 2).
