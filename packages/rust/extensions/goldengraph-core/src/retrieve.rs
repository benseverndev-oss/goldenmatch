//! Neighborhood retrieval: a 1-2 hop subgraph around seed entities.
//!
//! Edges are treated as undirected for expansion (a fact connects its two
//! endpoints regardless of subject/object order). An edge is included in the
//! result only when BOTH its endpoints fall inside the reached set, so the
//! subgraph is internally consistent. Output is deterministic: entities sorted
//! by `entity_id`, edges by `(subj, predicate, obj)`.

use crate::model::*;
use std::collections::BTreeSet;

/// Expand `hops` levels (clamped to {1, 2}) outward from `seeds` over the
/// undirected edge set, returning the induced `Subgraph`. Self-loops and cycles
/// terminate (the reached set only grows; expansion stops when a hop adds
/// nothing).
pub fn neighborhood(graph: &Graph, seeds: &[EntityId], hops: u8) -> Subgraph {
    let hops = hops.clamp(1, 2);
    let mut reached: BTreeSet<EntityId> = seeds.iter().copied().collect();
    for _ in 0..hops {
        // Collect this level's new neighbors before inserting, so each pass is a
        // clean BFS level (no mid-scan cascade into the next hop).
        let mut next: Vec<EntityId> = Vec::new();
        for e in &graph.edges {
            if reached.contains(&e.subj) && !reached.contains(&e.obj) {
                next.push(e.obj);
            }
            if reached.contains(&e.obj) && !reached.contains(&e.subj) {
                next.push(e.subj);
            }
        }
        let mut added = false;
        for id in next {
            added |= reached.insert(id);
        }
        if !added {
            break; // converged early; nothing new this hop
        }
    }
    let mut entities: Vec<EntityNode> = graph
        .entities
        .iter()
        .filter(|n| reached.contains(&n.entity_id))
        .cloned()
        .collect();
    entities.sort_by_key(|n| n.entity_id);
    let mut edges: Vec<Edge> = graph
        .edges
        .iter()
        .filter(|e| reached.contains(&e.subj) && reached.contains(&e.obj))
        .cloned()
        .collect();
    edges.sort_by(|a, b| (a.subj, &a.predicate, a.obj).cmp(&(b.subj, &b.predicate, b.obj)));
    Subgraph { entities, edges }
}

/// Entity ids whose canonical name OR any merged surface form equals `name`
/// exactly. Resolution may pick a surface form you wouldn't query by as the
/// canonical (the longest one), so matching members too keeps a resolved entity
/// findable by every name it was ever mentioned under. Result sorted by
/// `entity_id`.
pub fn seeds_by_name(graph: &Graph, name: &str) -> Vec<EntityId> {
    graph
        .entities
        .iter()
        .filter(|e| e.canonical_name == name || e.surface_names.iter().any(|s| s == name))
        .map(|e| e.entity_id)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn node(id: EntityId, name: &str) -> EntityNode {
        EntityNode {
            entity_id: id,
            canonical_name: name.into(),
            typ: "t".into(),
            members: vec![],
            surface_names: vec![name.into()],
        }
    }
    fn edge(s: EntityId, p: &str, o: EntityId) -> Edge {
        Edge {
            subj: s,
            predicate: p.into(),
            obj: o,
            source_refs: vec![],
        }
    }

    // entities 0,1,2,3; edges 0->1, 0->2, 2->3
    fn chain() -> Graph {
        Graph {
            entities: vec![node(0, "E0"), node(1, "E1"), node(2, "E2"), node(3, "E3")],
            edges: vec![edge(0, "r", 1), edge(0, "r", 2), edge(2, "r", 3)],
        }
    }

    #[test]
    fn one_hop_from_0_stops_before_entity_3() {
        let sub = neighborhood(&chain(), &[0], 1);
        let ids: Vec<EntityId> = sub.entities.iter().map(|e| e.entity_id).collect();
        assert_eq!(ids, vec![0, 1, 2]); // 3 not reached at 1 hop
        assert_eq!(sub.edges.len(), 2); // 0->1, 0->2 ; 2->3 excluded (3 not in set)
        assert!(sub.edges.iter().all(|e| e.obj != 3 && e.subj != 3));
    }

    #[test]
    fn two_hop_from_0_pulls_entity_3_and_its_edge() {
        let sub = neighborhood(&chain(), &[0], 2);
        let ids: Vec<EntityId> = sub.entities.iter().map(|e| e.entity_id).collect();
        assert_eq!(ids, vec![0, 1, 2, 3]);
        assert_eq!(sub.edges.len(), 3);
        assert!(sub.edges.iter().any(|e| e.subj == 2 && e.obj == 3));
    }

    #[test]
    fn self_loop_and_cycle_terminate_with_stable_output() {
        // self-loop 0->0 and a cycle 3->0 must not loop forever; output stable.
        let g = Graph {
            entities: vec![node(0, "E0"), node(1, "E1"), node(2, "E2"), node(3, "E3")],
            edges: vec![
                edge(0, "r", 0),
                edge(0, "r", 1),
                edge(0, "r", 2),
                edge(2, "r", 3),
                edge(3, "r", 0),
            ],
        };
        // undirected expansion: 3->0 makes 3 a 1-hop neighbor of 0 too
        let sub1 = neighborhood(&g, &[0], 1);
        let ids1: Vec<EntityId> = sub1.entities.iter().map(|e| e.entity_id).collect();
        assert_eq!(ids1, vec![0, 1, 2, 3]);
        assert_eq!(sub1.edges.len(), 5); // every edge's endpoints are reached

        // idempotent once the graph is fully covered + deterministic ordering
        let sub2 = neighborhood(&g, &[0], 2);
        let ids2: Vec<EntityId> = sub2.entities.iter().map(|e| e.entity_id).collect();
        assert_eq!(ids1, ids2);
        assert_eq!(sub1.edges, sub2.edges);
    }

    #[test]
    fn seeds_by_name_matches_any_surface_form_not_just_canonical() {
        // Dogfood-derived: a merged entity whose canonical is the LONGEST form
        // ("Apple Computer"), queried by a non-canonical surface form
        // ("Apple Inc.") that a user would actually type.
        let apple = EntityNode {
            entity_id: 0,
            canonical_name: "Apple Computer".into(),
            typ: "org".into(),
            members: vec![0, 1, 2],
            surface_names: vec!["Apple".into(), "Apple Computer".into(), "Apple Inc.".into()],
        };
        let g = Graph {
            entities: vec![apple],
            edges: vec![],
        };
        assert_eq!(seeds_by_name(&g, "Apple Computer"), vec![0]); // canonical
        assert_eq!(seeds_by_name(&g, "Apple Inc."), vec![0]); // non-canonical surface form
        assert_eq!(seeds_by_name(&g, "Apple"), vec![0]); // another surface form
        assert!(seeds_by_name(&g, "Microsoft").is_empty()); // absent name
    }
}
