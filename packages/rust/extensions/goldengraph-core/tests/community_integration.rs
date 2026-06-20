//! SP3 community-detection golden vectors — the cross-binding contract.
//!
//! Builds a resolved `Graph` from the fixture, runs `communities`, and asserts
//! the partition byte-equals the fixture (canonical JSON). Descriptive: freezes
//! the deterministic label-propagation output. SP5's WASM/C reuse this fixture.

use goldengraph_core::community::communities;
use goldengraph_core::model::{Edge, EntityNode, Graph};
use serde_json::{json, Value};

fn load() -> Value {
    let raw = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/community_golden.json"
    ))
    .expect("read community fixture");
    serde_json::from_str(&raw).expect("parse community fixture")
}

fn build_graph(v: &Value) -> Graph {
    let entities = v["graph"]["entities"]
        .as_array()
        .unwrap()
        .iter()
        .map(|e| EntityNode {
            entity_id: e["entity_id"].as_u64().unwrap() as u32,
            canonical_name: e["canonical_name"].as_str().unwrap().to_string(),
            typ: e["typ"].as_str().unwrap().to_string(),
            members: vec![],
            surface_names: e["surface_names"]
                .as_array()
                .unwrap()
                .iter()
                .map(|s| s.as_str().unwrap().to_string())
                .collect(),
        })
        .collect();
    let edges = v["graph"]["edges"]
        .as_array()
        .unwrap()
        .iter()
        .map(|e| Edge {
            subj: e["subj"].as_u64().unwrap() as u32,
            predicate: e["predicate"].as_str().unwrap().to_string(),
            obj: e["obj"].as_u64().unwrap() as u32,
            source_refs: e["source_refs"]
                .as_array()
                .unwrap()
                .iter()
                .map(|s| s.as_str().unwrap().to_string())
                .collect(),
        })
        .collect();
    Graph { entities, edges }
}

#[test]
fn community_golden_vectors_match() {
    let v = load();
    let g = build_graph(&v);
    let computed: Vec<Value> = communities(&g)
        .into_iter()
        .map(|c| json!({ "id": c.id, "members": c.members }))
        .collect();
    assert_eq!(
        serde_json::to_string(&Value::Array(computed)).unwrap(),
        serde_json::to_string(&v["expected_communities"]).unwrap(),
    );
}
