//! Golden parity: `fingerprint_json` reproduces the shared `fingerprint_golden
//! .json` oracle exactly. The SAME fixture is asserted by the TS/WASM surface
//! (`fingerprint-wasm.parity.test.ts`) and was generated from the Python
//! reference (`goldenmatch.core._hashing._fingerprint_py`), so a green run here
//! + there proves the canonicalization byte layout (field sort, type tags,
//! separators, float bits, `__`-drop) is identical on every surface.

use goldenmatch_fingerprint_core::fingerprint_json;
use serde_json::Value;

#[test]
fn reproduces_golden_fixture() {
    let raw = include_str!("../golden/fingerprint_golden.json");
    let cases: Vec<Value> = serde_json::from_str(raw).expect("golden fixture parses");
    assert!(cases.len() >= 10, "expected broad case coverage");
    for case in &cases {
        let name = case["name"].as_str().unwrap();
        let json = case["json"].as_str().unwrap();
        let expected = case["hash"].as_str().unwrap();
        let got = fingerprint_json(json).unwrap_or_else(|e| panic!("case {name:?}: {e}"));
        assert_eq!(got, expected, "case {name:?}: hash mismatch");
    }
}
