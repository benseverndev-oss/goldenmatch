//! Assert `connected_components` reproduces the shared golden fixture — the
//! cross-surface parity oracle. The `graph-wasm` TS surface
//! (`connected-components.parity.test.ts`) checks the same `graph_golden.json`,
//! so Rust / Python-native / DuckDB / Postgres / TS all agree on the partition.
//!
//! `connected_components` returns groups in HashMap order; the partition is
//! unique, so we canonicalize (members ascending, groups by min member) before
//! comparing — exactly what every caller does.

use goldenmatch_graph_core::connected_components;

fn canonical(mut comps: Vec<Vec<i64>>) -> Vec<Vec<i64>> {
    for c in comps.iter_mut() {
        c.sort_unstable();
    }
    comps.sort_by(|a, b| a.first().cmp(&b.first()));
    comps
}

#[test]
fn reproduces_golden_fixture() {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("golden/graph_golden.json");
    let raw = std::fs::read_to_string(&path).expect("read golden fixture");
    let cases: serde_json::Value = serde_json::from_str(&raw).expect("parse golden fixture");
    let cases = cases.as_array().expect("fixture is an array");
    assert!(cases.len() >= 5, "fixture should have edge coverage");

    for case in cases {
        let name = case["name"].as_str().unwrap_or("?");
        let edges: Vec<(i64, i64, f64)> = case["edges"]
            .as_array()
            .unwrap()
            .iter()
            .map(|e| {
                let e = e.as_array().unwrap();
                (e[0].as_i64().unwrap(), e[1].as_i64().unwrap(), 0.0)
            })
            .collect();
        let all_ids: Vec<i64> = case["all_ids"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_i64().unwrap())
            .collect();
        let want: Vec<Vec<i64>> = case["components"]
            .as_array()
            .unwrap()
            .iter()
            .map(|c| {
                c.as_array()
                    .unwrap()
                    .iter()
                    .map(|v| v.as_i64().unwrap())
                    .collect()
            })
            .collect();

        let got = canonical(connected_components(&edges, &all_ids));
        assert_eq!(got, canonical(want), "case {name:?}");
    }
}
