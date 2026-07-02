//! SP2 store golden vectors — the cross-binding contract.
//!
//! Replays a canonical sequence of appends, then asserts each `as_of(valid_t,
//! tx_t)` view byte-equals the fixture (canonical JSON), plus snapshot
//! round-trip stability. SP5's WASM/C bindings reuse this fixture for parity.

use goldengraph_core::model::{Edge, EntityNode};
use goldengraph_core::store::{BatchEdge, BatchEntity, GraphStore, StoreBatch};
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

// ---- node provenance: entity source_refs carried through as_of + accretive merge union ----

#[test]
fn as_of_carries_entity_source_refs() {
    let mut s = GraphStore::open(None).unwrap();
    s.append(StoreBatch {
        entities: vec![BatchEntity {
            local_id: 0,
            canonical_name: "Apple".into(),
            typ: "org".into(),
            surface_names: vec!["Apple".into()],
            record_keys: vec!["k:apple".into()],
            source_refs: vec!["docA".into()],
        }],
        edges: vec![BatchEdge {
            subj_local: 0,
            predicate: "is".into(),
            obj_local: 0,
            valid_from: 1,
            valid_to: None,
            source_refs: vec!["docA".into()],
        }],
        ingested_at: 1,
    });
    let g = s.as_of(i64::MAX, i64::MAX);
    let e = g
        .entities
        .iter()
        .find(|e| e.canonical_name == "Apple")
        .expect("Apple node present");
    assert_eq!(e.source_refs, vec!["docA".to_string()]);
}

#[test]
fn merge_unions_source_refs_accretively() {
    let mut s = GraphStore::open(None).unwrap();
    // batch 1: entity from docA (record key k:ibm)
    s.append(StoreBatch {
        entities: vec![BatchEntity {
            local_id: 0,
            canonical_name: "IBM".into(),
            typ: "org".into(),
            surface_names: vec!["IBM".into()],
            record_keys: vec!["k:ibm".into()],
            source_refs: vec!["docA".into()],
        }],
        edges: vec![BatchEdge {
            subj_local: 0,
            predicate: "is".into(),
            obj_local: 0,
            valid_from: 1,
            valid_to: None,
            source_refs: vec!["docA".into()],
        }],
        ingested_at: 1,
    });
    // batch 2: SAME record key (merges) from docB
    s.append(StoreBatch {
        entities: vec![BatchEntity {
            local_id: 0,
            canonical_name: "IBM".into(),
            typ: "org".into(),
            surface_names: vec!["IBM".into()],
            record_keys: vec!["k:ibm".into()],
            source_refs: vec!["docB".into()],
        }],
        edges: vec![BatchEdge {
            subj_local: 0,
            predicate: "is".into(),
            obj_local: 0,
            valid_from: 2,
            valid_to: None,
            source_refs: vec!["docB".into()],
        }],
        ingested_at: 2,
    });
    let g = s.as_of(i64::MAX, i64::MAX);
    let e = g
        .entities
        .iter()
        .find(|e| e.canonical_name == "IBM")
        .expect("IBM node present");
    // accretive union -- docA is NOT lost when docB merges in
    assert_eq!(
        e.source_refs,
        vec!["docA".to_string(), "docB".to_string()]
    );
}
