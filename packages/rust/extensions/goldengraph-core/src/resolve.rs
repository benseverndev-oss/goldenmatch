//! Resolution: turn mentions + mention-edges into an entity-space `Graph`.
//!
//! Two entry points share one downstream pipeline (`apply_resolution`):
//! Provided -- the caller hands in a `mention -> entity-id` map directly.
//! Native -- `resolve_native` derives the map by scoring within type-blocks
//! (`score-core`) and clustering via WCC (`graph-core`). See Task 4.

use crate::model::*;
use std::collections::{BTreeMap, HashMap};

/// Build the entity-space `Graph` from mentions + mention-edges + a
/// `mention -> entity-id` map.
///
/// Deterministic by construction: entities are ordered by `entity_id`
/// (`BTreeMap` over the groups), edges by `(subj, predicate, obj)`, and each
/// edge's `source_refs` are sorted + deduped. The canonical name of an entity
/// is its longest member name (ties broken toward the lowest mention id).
/// Mentions absent from `map`, and edges with an unmapped endpoint, are skipped.
pub fn apply_resolution(
    mentions: &[Mention],
    edges: &[MentionEdge],
    map: &HashMap<MentionId, EntityId>,
) -> Graph {
    // group mention ids by entity id
    let mut groups: BTreeMap<EntityId, Vec<MentionId>> = BTreeMap::new();
    for mid in 0..mentions.len() {
        if let Some(&eid) = map.get(&mid) {
            groups.entry(eid).or_default().push(mid);
        }
    }
    // entity nodes: canonical = longest member name (tie -> lowest mention id)
    let entities: Vec<EntityNode> = groups
        .iter()
        .map(|(&eid, members)| {
            let rep = *members
                .iter()
                .max_by_key(|&&m| (mentions[m].name.len(), usize::MAX - m))
                .unwrap();
            EntityNode {
                entity_id: eid,
                canonical_name: mentions[rep].name.clone(),
                typ: mentions[rep].typ.clone(),
                members: members.clone(),
            }
        })
        .collect();
    // edges: rewrite endpoints, dedup by (subj,predicate,obj), accumulate
    // source_refs (sorted, unique). BTreeMap keeps the output edge order stable.
    let mut acc: BTreeMap<(EntityId, String, EntityId), Vec<String>> = BTreeMap::new();
    for e in edges {
        let (Some(&s), Some(&o)) = (map.get(&e.subj), map.get(&e.obj)) else {
            continue; // skip unmapped
        };
        acc.entry((s, e.predicate.clone(), o))
            .or_default()
            .push(e.source_ref.clone());
    }
    let edges: Vec<Edge> = acc
        .into_iter()
        .map(|((subj, predicate, obj), mut refs)| {
            refs.sort();
            refs.dedup();
            Edge { subj, predicate, obj, source_refs: refs }
        })
        .collect();
    Graph { entities, edges }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::*;

    fn fixture() -> (Vec<Mention>, Vec<MentionEdge>) {
        // mentions 0,1 are the same entity ("Apple Inc"/"Apple"); 2 is "Jobs"; 3 is "iPhone"
        let mentions = vec![
            Mention { name: "Apple Inc".into(), typ: "org".into() }, // 0
            Mention { name: "Apple".into(), typ: "org".into() },     // 1
            Mention { name: "Jobs".into(), typ: "person".into() },   // 2
            Mention { name: "iPhone".into(), typ: "product".into() }, //3
        ];
        let edges = vec![
            MentionEdge { subj: 0, predicate: "founded_by".into(), obj: 2, source_ref: "c1".into() },
            MentionEdge { subj: 1, predicate: "released".into(), obj: 3, source_ref: "c2".into() },
        ];
        (mentions, edges)
    }

    #[test]
    fn provided_resolution_merges_nodes_and_keeps_both_edges() {
        let (mentions, edges) = fixture();
        // host says mentions 0 and 1 are entity 0; 2->1; 3->2
        let map = vec![(0usize, 0u32), (1, 0), (2, 1), (3, 2)].into_iter().collect();
        let g = apply_resolution(&mentions, &edges, &map);
        assert_eq!(g.entities.len(), 3); // 0+1 merged
        let apple = g.entities.iter().find(|e| e.entity_id == 0).unwrap();
        assert_eq!(apple.canonical_name, "Apple Inc"); // longest name
        assert_eq!(g.edges.len(), 2); // both facts attach to entity 0
        assert!(g.edges.iter().any(|e| e.subj == 0 && e.predicate == "founded_by" && e.obj == 1));
        assert!(g.edges.iter().any(|e| e.subj == 0 && e.predicate == "released" && e.obj == 2));
    }

    #[test]
    fn duplicate_edges_dedup_and_accumulate_sources() {
        let mentions = vec![
            Mention { name: "A Inc".into(), typ: "org".into() },
            Mention { name: "A".into(), typ: "org".into() },
            Mention { name: "B".into(), typ: "org".into() },
        ];
        let edges = vec![
            MentionEdge { subj: 0, predicate: "rel".into(), obj: 2, source_ref: "c1".into() },
            MentionEdge { subj: 1, predicate: "rel".into(), obj: 2, source_ref: "c2".into() }, // same after merge
        ];
        let map = vec![(0usize, 0u32), (1, 0), (2, 1)].into_iter().collect();
        let g = apply_resolution(&mentions, &edges, &map);
        assert_eq!(g.edges.len(), 1);
        assert_eq!(g.edges[0].source_refs, vec!["c1".to_string(), "c2".to_string()]);
    }
}
