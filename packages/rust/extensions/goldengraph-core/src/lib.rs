//! goldengraph-core -- pyo3-free knowledge-graph engine.
//!
//! Turns extracted mentions + mention-level relationships into a
//! resolution-merged entity graph, then answers 1-2 hop neighborhood queries.
//! Two resolution paths share one downstream pipeline:
//!   * `Provided` -- a host supplies the `mention -> entity-id` map directly.
//!   * `Native`   -- an explicit-config resolver scores within type-blocks
//!                   (`score-core`) and clusters via WCC (`graph-core`).
//!
//! Intentionally pyo3-free: the Python binding is the sibling
//! `goldengraph-native` crate. No LLM, no embeddings, no persistence (SP2+).

pub mod model;
pub mod resolve;
pub mod retrieve;

#[cfg(test)]
mod smoke {
    #[test]
    fn crate_builds() {
        assert_eq!(2 + 2, 4);
    }
}
