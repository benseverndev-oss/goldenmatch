//! SP3 — community detection over the resolved graph.
//!
//! Partitions a resolved `Graph`'s entities into communities via graph-core's
//! deterministic label-propagation kernel, run over the entity-space edges.
//! A "global" query composes this with SP1 retrieval: communities → per-community
//! subgraph via `neighborhood` over each community's members.
//!
//! Honest scope (carried from the spec): label propagation is a modest first
//! kernel — on small/dense graphs it yields community ≈ connected-component
//! granularity; Leiden is the future granularity upgrade. In-memory over a
//! resolved `Graph`; persisting communities in the SP2 store is a follow-up.

use goldenmatch_graph_core::label_propagation_communities;

use crate::model::{EntityId, Graph};

/// Fixed iteration cap for the query path — part of the deterministic contract
/// (it co-determines the partition the golden vectors freeze; never ad-hoc).
pub const COMMUNITY_MAX_ITERS: u32 = 100;

/// A detected community: a stable positional `id` plus its member entity ids.
#[derive(Clone, Debug, PartialEq)]
pub struct Community {
    /// Positional index after sorting communities by their minimum member.
    pub id: u32,
    /// Member entity ids, sorted ascending.
    pub members: Vec<EntityId>,
}

/// Partition a resolved `Graph`'s entities into communities. Entities with no
/// edges are singleton communities. Deterministic and independent of edge order
/// (the kernel sorts by min member; `id` is that positional index).
pub fn communities(graph: &Graph) -> Vec<Community> {
    let all_ids: Vec<i64> = graph.entities.iter().map(|e| e.entity_id as i64).collect();
    let edges: Vec<(i64, i64, f64)> = graph
        .edges
        .iter()
        .map(|e| (e.subj as i64, e.obj as i64, 1.0))
        .collect();
    let raw = label_propagation_communities(&edges, &all_ids, COMMUNITY_MAX_ITERS);
    raw.into_iter()
        .enumerate()
        .map(|(i, members)| Community {
            id: i as u32,
            members: members.into_iter().map(|x| x as EntityId).collect(),
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{Edge, EntityNode};

    fn node(id: EntityId) -> EntityNode {
        EntityNode {
            entity_id: id,
            canonical_name: format!("E{id}"),
            typ: "t".into(),
            members: vec![],
            surface_names: vec![format!("E{id}")],
        }
    }
    fn edge(s: EntityId, o: EntityId) -> Edge {
        Edge {
            subj: s,
            predicate: "r".into(),
            obj: o,
            source_refs: vec![],
        }
    }

    #[test]
    fn connected_entities_share_a_community_isolated_is_singleton() {
        // 0-1, 0-2 connected; 3 isolated
        let g = Graph {
            entities: vec![node(0), node(1), node(2), node(3)],
            edges: vec![edge(0, 1), edge(0, 2)],
        };
        let c = communities(&g);
        assert_eq!(
            c,
            vec![
                Community {
                    id: 0,
                    members: vec![0, 1, 2]
                },
                Community {
                    id: 1,
                    members: vec![3]
                },
            ]
        );
    }

    #[test]
    fn communities_order_independent_of_edge_order() {
        let mk = |edges: Vec<Edge>| Graph {
            entities: vec![node(0), node(1), node(2), node(3), node(4)],
            edges,
        };
        let a = communities(&mk(vec![edge(0, 1), edge(1, 2), edge(3, 4)]));
        let b = communities(&mk(vec![edge(3, 4), edge(2, 1), edge(1, 0)]));
        assert_eq!(a, b);
    }
}
