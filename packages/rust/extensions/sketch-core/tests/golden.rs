//! Assert the Rust kernel reproduces the shared golden-vector fixture generated
//! from the Python reference. This is the cross-language parity anchor: Python,
//! Rust, and TypeScript all check against the same `sketch_golden.json`.

use goldenmatch_sketch_core::{band_hashes, shingle, signature, ShingleMode};

fn fixture_path() -> std::path::PathBuf {
    // CARGO_MANIFEST_DIR = packages/rust/extensions/sketch-core
    std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../python/goldenmatch/tests/fixtures/sketch_golden.json")
}

fn ints(arr: &serde_json::Value) -> Vec<u64> {
    arr.as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().parse::<u64>().unwrap())
        .collect()
}

#[test]
fn rust_reproduces_golden_fixture() {
    let raw = std::fs::read_to_string(fixture_path()).expect("read golden fixture");
    let cases: serde_json::Value = serde_json::from_str(&raw).expect("parse golden fixture");
    let cases = cases.as_array().expect("fixture is an array");
    assert!(cases.len() >= 10, "fixture should have edge coverage");

    for case in cases {
        let text = case["text"].as_str().unwrap();
        let mode = ShingleMode::parse(case["mode"].as_str().unwrap()).unwrap();
        let k = case["k"].as_u64().unwrap() as usize;
        let num_perms = case["num_perms"].as_u64().unwrap() as usize;
        let num_bands = case["num_bands"].as_u64().unwrap() as usize;
        let seed = case["seed"].as_u64().unwrap();

        let sh = shingle(text, mode, k);
        assert_eq!(sh, ints(&case["shingles"]), "shingles for {text:?}");

        let sig = signature(&sh, num_perms, seed);
        assert_eq!(sig, ints(&case["signature"]), "signature for {text:?}");

        let bands = band_hashes(&sig, num_bands);
        assert_eq!(
            bands,
            ints(&case["band_hashes"]),
            "band_hashes for {text:?}"
        );
    }
}
