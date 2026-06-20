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

/// Config for the native explicit-config resolver.
///
/// `scorer_id` indexes `score-core`'s `score_one` dispatch
/// (0=jaro_winkler, 1=levenshtein, 2=token_sort, 3=exact). `threshold` is on
/// the [0,1] scale -- every `score_one` scorer_id returns [0,1].
#[derive(Clone, Debug)]
pub struct NativeConfig {
    pub scorer_id: u8,
    pub threshold: f64,
}

/// How to obtain the `mention -> entity-id` map feeding `apply_resolution`.
pub enum ResolutionMode {
    /// The caller supplies the map directly.
    Provided(HashMap<MentionId, EntityId>),
    /// Derive the map natively via score-core + graph-core.
    Native(NativeConfig),
}

/// Native resolver: block by `type`, score all within-block pairs with
/// `score-core`, keep pairs at or above `threshold`, cluster them with
/// `graph-core`'s WCC, and assign each cluster a stable `EntityId`. Reuses the
/// kernels wholesale -- no new entity-resolution logic lives here.
///
/// Determinism: WCC grouping is independent of edge order, and clusters are
/// numbered by their minimum mention id, so the returned map is stable across
/// runs (and across the non-deterministic block-iteration order).
pub fn resolve_native(mentions: &[Mention], cfg: &NativeConfig) -> HashMap<MentionId, EntityId> {
    // 1. block by type
    let mut blocks: HashMap<&str, Vec<MentionId>> = HashMap::new();
    for (i, m) in mentions.iter().enumerate() {
        blocks.entry(m.typ.as_str()).or_default().push(i);
    }
    // 2. all-pairs score within each block -> edges (i64, i64, f64) at/above threshold
    let mut pair_edges: Vec<(i64, i64, f64)> = Vec::new();
    for ids in blocks.values() {
        for a in 0..ids.len() {
            for b in (a + 1)..ids.len() {
                let (i, j) = (ids[a], ids[b]);
                let s = goldenmatch_score_core::score_one(
                    cfg.scorer_id,
                    &mentions[i].name,
                    &mentions[j].name,
                );
                if s >= cfg.threshold {
                    pair_edges.push((i as i64, j as i64, s));
                }
            }
        }
    }
    // 3. WCC over every mention id -> clusters (singletons returned as 1-element)
    let all_ids: Vec<i64> = (0..mentions.len() as i64).collect();
    let mut clusters = goldenmatch_graph_core::connected_components(&pair_edges, &all_ids);
    // 4. number clusters by min mention id -> stable EntityId
    clusters.sort_by_key(|c| *c.iter().min().unwrap());
    let mut map = HashMap::new();
    for (eid, cluster) in clusters.iter().enumerate() {
        for &mid in cluster {
            map.insert(mid as MentionId, eid as EntityId);
        }
    }
    map
}

#[cfg(test)]
mod tests {
    use super::*;

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

    #[test]
    fn native_resolver_merges_apple_via_score_and_wcc() {
        let (mentions, edges) = fixture();
        // scorer_id 0 = jaro_winkler (score-core `score_one` match arms; jw of
        // "Apple Inc"/"Apple" ~= 0.91, above the 0.85 threshold).
        let cfg = NativeConfig { scorer_id: 0, threshold: 0.85 };
        let map = resolve_native(&mentions, &cfg);
        // Apple Inc (0) + Apple (1) cluster; Jobs (2) and iPhone (3) stay singletons.
        assert_eq!(map[&0], map[&1]);
        assert_ne!(map[&0], map[&2]);
        assert_ne!(map[&0], map[&3]);
        assert_ne!(map[&2], map[&3]);
        // same end state as the Provided path: 3 entities, both edges.
        let g = apply_resolution(&mentions, &edges, &map);
        assert_eq!(g.entities.len(), 3);
        assert_eq!(g.edges.len(), 2);
    }
}
