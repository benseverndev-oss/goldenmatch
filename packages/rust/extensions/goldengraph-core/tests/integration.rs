//! Differentiator test (SP1 thesis): resolution is the difference between a
//! complete and a half answer.
//!
//! Same mentions + edges, two resolution maps loaded from a named fixture:
//! `exact` keeps "Apple Inc" / "Apple" split (each its own entity); `resolved`
//! merges them into entity 0. A 1-hop query from the Apple entity returns BOTH
//! facts under `resolved` but only ONE under `exact` -- the headline claim.

use std::collections::HashMap;

use goldengraph_core::build_graph;
use goldengraph_core::model::{Edge, EntityId, EntityNode, Mention, MentionEdge, MentionId};
use goldengraph_core::resolve::{apply_resolution, ResolutionMode};
use goldengraph_core::retrieve::neighborhood;
use serde_json::{json, Value};

fn load() -> Value {
    let raw = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/resolution_split_merge.json"
    ))
    .expect("read fixture");
    serde_json::from_str(&raw).expect("parse fixture")
}

fn mentions(v: &Value) -> Vec<Mention> {
    v["mentions"]
        .as_array()
        .unwrap()
        .iter()
        .map(|m| Mention {
            name: m["name"].as_str().unwrap().to_string(),
            typ: m["typ"].as_str().unwrap().to_string(),
        })
        .collect()
}

fn edges(v: &Value) -> Vec<MentionEdge> {
    v["edges"]
        .as_array()
        .unwrap()
        .iter()
        .map(|e| MentionEdge {
            subj: e["subj"].as_u64().unwrap() as MentionId,
            predicate: e["predicate"].as_str().unwrap().to_string(),
            obj: e["obj"].as_u64().unwrap() as MentionId,
            source_ref: e["source_ref"].as_str().unwrap().to_string(),
        })
        .collect()
}

fn map(v: &Value, name: &str) -> HashMap<MentionId, EntityId> {
    v["maps"][name]
        .as_object()
        .unwrap()
        .iter()
        .map(|(k, val)| (k.parse::<MentionId>().unwrap(), val.as_u64().unwrap() as EntityId))
        .collect()
}

#[test]
fn resolved_one_hop_finds_both_facts() {
    let v = load();
    let g = apply_resolution(&mentions(&v), &edges(&v), &map(&v, "resolved"));
    // entity 0 is the merged Apple node (Apple Inc + Apple)
    let apple = g.entities.iter().find(|e| e.entity_id == 0).unwrap();
    assert_eq!(apple.canonical_name, "Apple Inc");
    assert_eq!(apple.members.len(), 2);
    let sub = neighborhood(&g, &[0], 1);
    let preds: Vec<&str> = sub.edges.iter().map(|e| e.predicate.as_str()).collect();
    assert!(preds.contains(&"founded_by"), "got {preds:?}");
    assert!(preds.contains(&"released"), "got {preds:?}");
    assert_eq!(sub.edges.len(), 2); // BOTH facts reachable from the merged entity
}

#[test]
fn exact_one_hop_finds_only_half_the_facts() {
    let v = load();
    let g = apply_resolution(&mentions(&v), &edges(&v), &map(&v, "exact"));
    // entity 0 is "Apple Inc" alone; "Apple" is the separate entity 1
    let apple_inc = g.entities.iter().find(|e| e.entity_id == 0).unwrap();
    assert_eq!(apple_inc.canonical_name, "Apple Inc");
    assert_eq!(apple_inc.members, vec![0]);
    let sub = neighborhood(&g, &[0], 1);
    let preds: Vec<&str> = sub.edges.iter().map(|e| e.predicate.as_str()).collect();
    // only founded_by Jobs; the `released` fact hangs off the separate "Apple" (entity 1)
    assert_eq!(preds, vec!["founded_by"]);
}

// ---- Golden vectors (cross-binding parity contract) ------------------------

fn load_golden() -> Value {
    let raw = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/goldengraph_golden.json"
    ))
    .expect("read golden fixture");
    serde_json::from_str(&raw).expect("parse golden fixture")
}

/// Canonicalize an entity/edge view to a `serde_json::Value` in the engine's
/// deterministic shape, so it can be compared byte-for-byte against the fixture.
fn view_to_value(entities: &[EntityNode], edges: &[Edge]) -> Value {
    let ents: Vec<Value> = entities
        .iter()
        .map(|e| {
            json!({
                "entity_id": e.entity_id,
                "canonical_name": e.canonical_name,
                "typ": e.typ,
                "members": e.members,
            })
        })
        .collect();
    let eds: Vec<Value> = edges
        .iter()
        .map(|e| {
            json!({
                "subj": e.subj,
                "predicate": e.predicate,
                "obj": e.obj,
                "source_refs": e.source_refs,
            })
        })
        .collect();
    json!({ "entities": ents, "edges": eds })
}

/// `serde_json::Value` -> canonical string (default `Value::Object` is a sorted
/// `BTreeMap`, so object keys serialize in a stable order on both sides).
fn canonical(v: &Value) -> String {
    serde_json::to_string(v).unwrap()
}

#[test]
fn golden_vectors_match() {
    let v = load_golden();
    let map: HashMap<MentionId, EntityId> = v["resolution"]
        .as_object()
        .unwrap()
        .iter()
        .map(|(k, val)| (k.parse::<MentionId>().unwrap(), val.as_u64().unwrap() as EntityId))
        .collect();
    let g = build_graph(&mentions(&v), &edges(&v), ResolutionMode::Provided(map));

    // Full graph: merge (3-way) + edge dedup (accumulated source_refs).
    let computed = view_to_value(&g.entities, &g.edges);
    assert_eq!(
        canonical(&computed),
        canonical(&v["expected_graph"]),
        "graph mismatch"
    );

    // Each query subgraph (1-hop limited, 2-hop expanded).
    for q in v["queries"].as_array().unwrap() {
        let seeds: Vec<EntityId> = q["seeds"]
            .as_array()
            .unwrap()
            .iter()
            .map(|x| x.as_u64().unwrap() as EntityId)
            .collect();
        let hops = q["hops"].as_u64().unwrap() as u8;
        let sub = neighborhood(&g, &seeds, hops);
        let computed = view_to_value(&sub.entities, &sub.edges);
        assert_eq!(
            canonical(&computed),
            canonical(&q["expected"]),
            "query `{}` mismatch",
            q["name"].as_str().unwrap()
        );
    }
}
