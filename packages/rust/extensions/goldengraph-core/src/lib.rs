//! goldengraph-core -- pyo3-free knowledge-graph engine.
//!
//! Turns extracted mentions + mention-level relationships into a
//! resolution-merged entity graph, then answers 1-2 hop neighborhood queries.
//! Two resolution paths share one downstream pipeline:
//! `Provided` -- a host supplies the `mention -> entity-id` map directly.
//! `Native` -- an explicit-config resolver scores within type-blocks
//! (`score-core`) and clusters via WCC (`graph-core`).
//!
//! Intentionally pyo3-free: the Python binding is the sibling
//! `goldengraph-native` crate. No LLM, no embeddings, no persistence (SP2+).

pub mod model;
pub mod resolve;
pub mod retrieve;
pub mod store;

use model::{Graph, Mention, MentionEdge};
use resolve::{apply_resolution, resolve_native, ResolutionMode};

/// Build the entity-space graph from mentions + mention-edges under either
/// resolution mode. `Provided` uses the supplied map directly; `Native` derives
/// one via score-core + graph-core. Both feed the same `apply_resolution`, so
/// the downstream graph shape is identical regardless of how resolution ran.
pub fn build_graph(mentions: &[Mention], edges: &[MentionEdge], mode: ResolutionMode) -> Graph {
    match mode {
        ResolutionMode::Provided(map) => apply_resolution(mentions, edges, &map),
        ResolutionMode::Native(cfg) => {
            let map = resolve_native(mentions, &cfg);
            apply_resolution(mentions, edges, &map)
        }
    }
}

#[cfg(test)]
mod smoke {
    #[test]
    fn crate_builds() {
        assert_eq!(2 + 2, 4);
    }
}
