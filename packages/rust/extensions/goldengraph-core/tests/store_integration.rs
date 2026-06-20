//! SP2 store golden vectors — the cross-binding contract.
//!
//! Replays a canonical sequence of appends, then asserts each `as_of(valid_t,
//! tx_t)` view byte-equals the fixture (canonical JSON), plus snapshot
//! round-trip stability. SP5's WASM/C bindings reuse this fixture for parity.

use goldengraph_core::model::{Edge, EntityNode};
use goldengraph_core::store::{GraphStore, StoreBatch};
use serde_json::{json, Value};

fn load() -> Value {
    let raw = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/store_golden.json"
    ))
    .expect("read store fixture");
    serde_json::from_str(&raw).expect("parse store fixture")
}

/// Canonicalize a view's entities+edges into a `Value` matching the fixture's
/// `expected` shape (entity_id/canonical_name/typ/surface_names; subj/predicate/
/// obj/source_refs). Mirrors the SP1 integration-test pattern.
fn view_to_value(entities: &[EntityNode], edges: &[Edge]) -> Value {
    let ents: Vec<Value> = entities
        .iter()
        .map(|e| {
            json!({
                "entity_id": e.entity_id,
                "canonical_name": e.canonical_name,
                "typ": e.typ,
                "surface_names": e.surface_names,
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

fn canonical(v: &Value) -> String {
    serde_json::to_string(v).unwrap()
}

#[test]
fn store_golden_vectors_match() {
    let v = load();
    let mut store = GraphStore::open(None).unwrap();
    for b in v["batches"].as_array().unwrap() {
        let batch: StoreBatch = serde_json::from_value(b.clone()).expect("deserialize StoreBatch");
        store.append(batch);
    }

    for q in v["queries"].as_array().unwrap() {
        let valid_t = q["valid_t"].as_i64().unwrap();
        let tx_t = q["tx_t"].as_i64().unwrap();
        let view = store.as_of(valid_t, tx_t);
        let computed = view_to_value(&view.entities, &view.edges);
        assert_eq!(
            canonical(&computed),
            canonical(&q["expected"]),
            "query `{}` mismatch",
            q["name"].as_str().unwrap()
        );
    }

    // snapshot is portable + stable: reopening re-serializes byte-identical.
    let snap = store.snapshot();
    assert_eq!(snap, GraphStore::open(Some(&snap)).unwrap().snapshot());
}
