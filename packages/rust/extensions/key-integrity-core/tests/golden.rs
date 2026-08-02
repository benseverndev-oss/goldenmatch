//! Golden parity: the structural kernel reproduces the shared
//! `key_integrity_golden.json` oracle, which was generated from the Python
//! reference `certify_key_integrity` (structural tier). The SAME fixture is
//! asserted by the TS/WASM surface, so a green run here + there proves the
//! group-by uniqueness + fan-out reduction is identical on every surface.

use goldenmatch_key_integrity_core::certify_structural_json;
use serde_json::Value;

#[test]
fn reproduces_golden_fixture() {
    let raw = include_str!("../golden/key_integrity_golden.json");
    let doc: Value = serde_json::from_str(raw).expect("golden fixture parses");
    let cases = doc["cases"].as_array().expect("cases array");
    assert!(cases.len() >= 6, "expected broad case coverage");

    for case in cases {
        let name = case["name"].as_str().unwrap();
        let input = serde_json::to_string(&case["input"]).unwrap();
        let got: Value = serde_json::from_str(
            &certify_structural_json(&input).unwrap_or_else(|e| panic!("case {name:?}: {e}")),
        )
        .unwrap();
        let exp = &case["expected"];

        assert_eq!(
            got["n_key_groups"], exp["n_key_groups"],
            "case {name:?}: n_key_groups"
        );
        assert_eq!(
            got["duplicate_key_groups"], exp["duplicate_key_groups"],
            "case {name:?}: duplicate_key_groups"
        );
        assert_eq!(
            got["is_unique_at_grain"], exp["is_unique_at_grain"],
            "case {name:?}: is_unique_at_grain"
        );
        assert!(
            (got["max_fan_out"].as_f64().unwrap() - exp["max_fan_out"].as_f64().unwrap()).abs()
                < 1e-9,
            "case {name:?}: max_fan_out"
        );

        let got_fan = got["measure_fan_out"].as_object().unwrap();
        let exp_fan = exp["measure_fan_out"].as_object().unwrap();
        assert_eq!(
            got_fan.len(),
            exp_fan.len(),
            "case {name:?}: measure_fan_out arity"
        );
        for (k, ev) in exp_fan {
            let gv = got_fan
                .get(k)
                .unwrap_or_else(|| panic!("case {name:?}: missing measure {k}"));
            assert!(
                (gv.as_f64().unwrap() - ev.as_f64().unwrap()).abs() < 1e-9,
                "case {name:?}: measure_fan_out[{k}]"
            );
        }
    }
}
