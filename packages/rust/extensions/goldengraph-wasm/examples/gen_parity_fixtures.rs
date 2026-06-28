//! Author the cross-surface parity fixtures from the HOST boundary
//! (`*_impl` == `goldengraph-core`), the SAME kernel the wasm32 build and the
//! Python `goldengraph_native` wheel wrap. Emits the single source-of-truth
//! `{name, fn, args, expected}` set the TS wasm parity test asserts against.
//!
//! Run (writes the committed fixture file):
//!   cargo run -p goldengraph-wasm --example gen_parity_fixtures > \
//!     ../../typescript/goldengraph/tests/parity/fixtures/goldengraph/queries.json
//!
//! Canonicalized + idempotent across runs, so the CI staleness guard stays
//! stable (the kernel's entity/edge ordering can fall out of hash-map order).

use serde_json::{json, Value};

/// Canonicalize a Graph/Subgraph Value: sort entities by id, members +
/// surface_names within each, sort edges by (subj,predicate,obj), source_refs.
fn canon_graph(mut g: Value) -> Value {
    if let Some(es) = g["entities"].as_array() {
        let mut v: Vec<Value> = es
            .iter()
            .map(|e| {
                let mut e = e.clone();
                if let Some(m) = e["members"].as_array() {
                    let mut m: Vec<i64> = m.iter().map(|x| x.as_i64().unwrap()).collect();
                    m.sort_unstable();
                    e["members"] = json!(m);
                }
                if let Some(s) = e["surface_names"].as_array() {
                    let mut s: Vec<String> =
                        s.iter().map(|x| x.as_str().unwrap().to_string()).collect();
                    s.sort();
                    e["surface_names"] = json!(s);
                }
                e
            })
            .collect();
        v.sort_by_key(|e| e["entity_id"].as_i64().unwrap());
        g["entities"] = json!(v);
    }
    if let Some(es) = g["edges"].as_array() {
        let mut v: Vec<Value> = es
            .iter()
            .map(|e| {
                let mut e = e.clone();
                if let Some(s) = e["source_refs"].as_array() {
                    let mut s: Vec<String> =
                        s.iter().map(|x| x.as_str().unwrap().to_string()).collect();
                    s.sort();
                    e["source_refs"] = json!(s);
                }
                e
            })
            .collect();
        v.sort_by(|a, b| {
            (
                a["subj"].as_i64().unwrap(),
                a["predicate"].as_str().unwrap(),
                a["obj"].as_i64().unwrap(),
            )
                .cmp(&(
                    b["subj"].as_i64().unwrap(),
                    b["predicate"].as_str().unwrap(),
                    b["obj"].as_i64().unwrap(),
                ))
        });
        g["edges"] = json!(v);
    }
    g
}

fn m(name: &str, typ: &str) -> Value {
    json!({ "name": name, "typ": typ })
}
fn me(subj: usize, predicate: &str, obj: usize, source_ref: &str) -> Value {
    json!({ "subj": subj, "predicate": predicate, "obj": obj, "source_ref": source_ref })
}

fn build_graph(mentions: &Value, edges: &Value, resolution: &Value) -> Value {
    let g = goldengraph_wasm::build_graph_impl(
        &serde_json::to_string(mentions).unwrap(),
        &serde_json::to_string(edges).unwrap(),
        &serde_json::to_string(resolution).unwrap(),
    )
    .expect("build_graph");
    canon_graph(serde_json::from_str(&g).unwrap())
}

fn main() {
    let mut cases: Vec<Value> = Vec::new();

    // Shared scenario: 3 mentions (Apple Inc / Apple merge; Tim Cook distinct),
    // one ceo_of edge, a caller-provided resolution.
    let mentions = json!([
        m("Apple Inc", "Company"),
        m("Apple", "Company"),
        m("Tim Cook", "Person"),
    ]);
    let edges = json!([me(2, "ceo_of", 0, "doc1")]);
    let resolution = json!({ "0": 0, "1": 0, "2": 1 });

    // 1. build_graph (provided resolution).
    let graph = build_graph(&mentions, &edges, &resolution);
    cases.push(json!({
        "name": "build_graph_provided",
        "fn": "build_graph",
        "args": { "mentions": mentions, "edges": edges, "resolution": resolution },
        "expected": graph,
    }));

    // 2. neighborhood (1 hop around entity 0).
    let nb = goldengraph_wasm::neighborhood_impl(
        &serde_json::to_string(&graph).unwrap(),
        &serde_json::to_string(&json!([0])).unwrap(),
        1,
    )
    .expect("neighborhood");
    cases.push(json!({
        "name": "neighborhood_e0_1hop",
        "fn": "neighborhood",
        "args": { "graph": graph, "seeds": [0], "hops": 1 },
        "expected": canon_graph(serde_json::from_str(&nb).unwrap()),
    }));

    // 3. seeds_by_name ("Apple" -> entity 0 by surface form).
    let seeds =
        goldengraph_wasm::seeds_by_name_impl(&serde_json::to_string(&graph).unwrap(), "Apple")
            .expect("seeds_by_name");
    let mut seed_ids: Vec<i64> = serde_json::from_str::<Vec<i64>>(&seeds).unwrap();
    seed_ids.sort_unstable();
    cases.push(json!({
        "name": "seeds_by_name_apple",
        "fn": "seeds_by_name",
        "args": { "graph": graph, "name": "Apple" },
        "expected": seed_ids,
    }));

    // 4. communities (connected pair -> one community; deterministic kernel).
    let comms = goldengraph_wasm::communities_impl(&serde_json::to_string(&graph).unwrap())
        .expect("communities");
    cases.push(json!({
        "name": "communities_connected",
        "fn": "communities",
        "args": { "graph": graph },
        "expected": serde_json::from_str::<Value>(&comms).unwrap(),
    }));

    // 5. communities on a disconnected graph (two singletons) — multi-community.
    let m2 = json!([m("Acme", "Company"), m("Globex", "Company")]);
    let e2 = json!([]);
    let r2 = json!({ "0": 0, "1": 1 });
    let g2 = build_graph(&m2, &e2, &r2);
    let comms2 = goldengraph_wasm::communities_impl(&serde_json::to_string(&g2).unwrap())
        .expect("communities2");
    cases.push(json!({
        "name": "communities_disconnected",
        "fn": "communities",
        "args": { "graph": g2 },
        "expected": serde_json::from_str::<Value>(&comms2).unwrap(),
    }));

    // ---- Bitemporal store: append -> append(merge via record_key) -> as_of / history.
    let be = |local_id: u32, name: &str, typ: &str, surf: Value, keys: Value| {
        json!({"local_id": local_id, "canonical_name": name, "typ": typ,
               "surface_names": surf, "record_keys": keys})
    };
    let bed = |s: u32, p: &str, o: u32, vf: i64, refs: Value| {
        json!({"subj_local": s, "predicate": p, "obj_local": o,
               "valid_from": vf, "valid_to": Value::Null, "source_refs": refs})
    };
    // Batch 1 @ t=100: Apple Inc + Tim Cook, one ceo_of edge.
    let batch1 = json!({
        "entities": [
            be(0, "Apple Inc", "Company", json!(["Apple Inc"]), json!(["k_apple"])),
            be(1, "Tim Cook", "Person", json!(["Tim Cook"]), json!(["k_tim"])),
        ],
        "edges": [bed(1, "ceo_of", 0, 100, json!(["doc1"]))],
        "ingested_at": 100,
    });
    // Batch 2 @ t=200: same record_key k_apple, new surface "Apple".
    let batch2 = json!({
        "entities": [be(0, "Apple", "Company", json!(["Apple"]), json!(["k_apple"]))],
        "edges": [],
        "ingested_at": 200,
    });

    let snap1 = goldengraph_wasm::store_append_impl("", &serde_json::to_string(&batch1).unwrap())
        .expect("append1");
    let snap2 =
        goldengraph_wasm::store_append_impl(&snap1, &serde_json::to_string(&batch2).unwrap())
            .expect("append2");

    cases.push(json!({
        "name": "store_append_fresh",
        "fn": "store_append",
        "args": { "snapshot": "", "batch": batch1 },
        "expected": serde_json::from_str::<Value>(&snap1).unwrap(),
    }));
    cases.push(json!({
        "name": "store_append_merge",
        "fn": "store_append",
        "args": { "snapshot": serde_json::from_str::<Value>(&snap1).unwrap(), "batch": batch2 },
        "expected": serde_json::from_str::<Value>(&snap2).unwrap(),
    }));

    // as_of the current view (valid + tx both after the last ingest).
    let g_asof = goldengraph_wasm::store_as_of_impl(&snap2, 250, 250).expect("as_of");
    cases.push(json!({
        "name": "store_as_of_current",
        "fn": "store_as_of",
        "args": { "snapshot": serde_json::from_str::<Value>(&snap2).unwrap(), "valid_t": 250, "tx_t": 250 },
        "expected": canon_graph(serde_json::from_str(&g_asof).unwrap()),
    }));

    // history of the first entity id in the snapshot.
    let snap2v: Value = serde_json::from_str(&snap2).unwrap();
    let first_id: u64 = snap2v["entities"]
        .as_object()
        .unwrap()
        .keys()
        .next()
        .unwrap()
        .parse()
        .unwrap();
    let hist = goldengraph_wasm::store_history_impl(&snap2, first_id).expect("history");
    cases.push(json!({
        "name": "store_history_first",
        "fn": "store_history",
        "args": { "snapshot": snap2v, "id": first_id },
        "expected": serde_json::from_str::<Value>(&hist).unwrap(),
    }));

    println!(
        "{}",
        serde_json::to_string_pretty(&json!({ "cases": cases })).unwrap()
    );
}
