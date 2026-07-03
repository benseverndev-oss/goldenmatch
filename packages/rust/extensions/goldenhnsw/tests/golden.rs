//! Golden cross-surface parity: the core kernel must reproduce
//! `golden/hnsw_vectors.json` exactly. The SAME fixture is copied into the
//! TS package (`tests/parity/fixtures/hnsw/`) by
//! `scripts/build_goldenhnsw_wasm.mjs`, so the Rust core, the Python wheel, and
//! the TS/WASM surface all validate against one canonical set of expected
//! neighbors — parity is asserted, not assumed.

use goldenhnsw::{HnswIndex, HnswParams};
use serde_json::Value;
use std::path::PathBuf;

#[test]
fn reproduces_golden_vectors() {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("golden/hnsw_vectors.json");
    let text = std::fs::read_to_string(&path).expect("read golden fixture");
    let fx: Value = serde_json::from_str(&text).expect("parse golden fixture");

    let p = &fx["params"];
    let dim = p["dim"].as_u64().unwrap() as usize;
    let k = p["k"].as_u64().unwrap() as usize;
    let params = HnswParams {
        m: p["m"].as_u64().unwrap() as usize,
        ef_construction: p["ef_construction"].as_u64().unwrap() as usize,
        ef_search: p["ef_search"].as_u64().unwrap() as usize,
        seed: p["seed"].as_u64().unwrap(),
    };
    let n = fx["n"].as_u64().unwrap() as usize;
    let corpus: Vec<f32> = fx["corpus"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_f64().unwrap() as f32)
        .collect();
    assert_eq!(corpus.len(), n * dim);

    let mut idx = HnswIndex::new(dim, params);
    for row in corpus.chunks_exact(dim) {
        idx.add(row);
    }
    assert_eq!(idx.len(), n);

    let queries: Vec<f32> = fx["queries"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_f64().unwrap() as f32)
        .collect();
    let expected = fx["expected"].as_array().unwrap();

    for (qi, q) in queries.chunks_exact(dim).enumerate() {
        let got = idx.search(q, k);
        let want = expected[qi].as_array().unwrap();
        assert_eq!(got.len(), want.len(), "query {qi}: result length");
        for (j, hit) in want.iter().enumerate() {
            let want_id = hit[0].as_u64().unwrap() as u32;
            let want_score = hit[1].as_f64().unwrap() as f32;
            assert_eq!(got[j].0, want_id, "query {qi} rank {j}: id");
            assert!(
                (got[j].1 - want_score).abs() < 1e-6,
                "query {qi} rank {j}: score {} vs golden {want_score}",
                got[j].1
            );
        }
    }
}
