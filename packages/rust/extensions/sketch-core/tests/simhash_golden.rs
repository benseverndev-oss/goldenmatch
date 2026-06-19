//! Assert the Rust SimHash kernel reproduces the shared golden-vector fixture
//! generated from the Python reference (#1082). This is the cross-language
//! parity anchor: Python, Rust, and TypeScript all check against the same
//! `sketch_simhash_golden.json`.

use goldenmatch_sketch_core::{simhash_band_hashes, simhash_signature};

fn fixture_path() -> std::path::PathBuf {
    // CARGO_MANIFEST_DIR = packages/rust/extensions/sketch-core
    std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../python/goldenmatch/tests/fixtures/sketch_simhash_golden.json")
}

/// Signature bytes (0/1) from a JSON int array.
fn sig_bytes(arr: &serde_json::Value) -> Vec<u8> {
    arr.as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_u64().unwrap() as u8)
        .collect()
}

/// Band hashes (u64) from a JSON decimal-string array.
fn u64s(arr: &serde_json::Value) -> Vec<u64> {
    arr.as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().parse::<u64>().unwrap())
        .collect()
}

/// Vector (f64) from a JSON number array.
fn f64s(arr: &serde_json::Value) -> Vec<f64> {
    arr.as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_f64().unwrap())
        .collect()
}

#[test]
fn rust_reproduces_simhash_golden_fixture() {
    let raw = std::fs::read_to_string(fixture_path()).expect("read simhash golden fixture");
    let cases: serde_json::Value =
        serde_json::from_str(&raw).expect("parse simhash golden fixture");
    let cases = cases.as_array().expect("fixture is an array");
    assert!(cases.len() >= 10, "fixture should have edge coverage");

    for case in cases {
        let label = case["label"].as_str().unwrap();
        let vector = f64s(&case["vector"]);
        let num_planes = case["num_planes"].as_u64().unwrap() as usize;
        let num_bands = case["num_bands"].as_u64().unwrap() as usize;
        let seed = case["seed"].as_u64().unwrap();

        let sig = simhash_signature(&vector, num_planes, seed);
        assert_eq!(
            sig,
            sig_bytes(&case["signature"]),
            "signature for {label:?}"
        );

        let bands = simhash_band_hashes(&sig, num_bands);
        assert_eq!(
            bands,
            u64s(&case["band_hashes"]),
            "band_hashes for {label:?}"
        );
    }
}
