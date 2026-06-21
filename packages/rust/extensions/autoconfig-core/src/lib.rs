//! Deterministic auto-config decisions shared across surfaces (no pyo3).
//! Port oracle: docs/superpowers/specs/2026-06-20-autoconfig-native-core-design.md
pub mod classify;
pub mod planner;
#[cfg(feature = "arrow")]
pub mod profile;
// Function re-exports (decide_plan, classify_columns) are ADDED in A3 / B4.
