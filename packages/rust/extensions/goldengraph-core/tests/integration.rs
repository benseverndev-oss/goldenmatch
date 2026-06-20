//! Differentiator test (SP1 thesis): resolution is the difference between a
//! complete and a half answer.
//!
//! Same mentions + edges, two resolution maps loaded from a named fixture:
//! `exact` keeps "Apple Inc" / "Apple" split (each its own entity); `resolved`
//! merges them into entity 0. A 1-hop query from the Apple entity returns BOTH
//! facts under `resolved` but only ONE under `exact` -- the headline claim.

use std::collections::HashMap;

use goldengraph_core::model::{EntityId, Mention, MentionEdge, MentionId};
use goldengraph_core::resolve::apply_resolution;
use goldengraph_core::retrieve::neighborhood;
use serde_json::Value;

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
