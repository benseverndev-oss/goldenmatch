//! Replays the golden vectors through the *_json wrappers. These fixtures ARE the
//! cross-surface parity contract: SP2 (Python) and SP3 (TS) fallbacks must reproduce
//! these exact JSON values (VALUE + key order, which preserve_order maintains).
use goldenpipe_core::json::*;
use serde_json::Value;

fn load(name: &str) -> Vec<Value> {
    let path = format!("{}/tests/vectors/{}.json", env!("CARGO_MANIFEST_DIR"), name);
    let s = std::fs::read_to_string(&path).unwrap_or_else(|_| panic!("missing {path}"));
    serde_json::from_str(&s).unwrap()
}

/// Each case: {"input": <json>, "expected": <json>}. We call `f(input_string)` and
/// compare the PARSED result Value to `expected` (value equality; key order is enforced
/// separately by the json.rs insertion-order test).
fn run(name: &str, f: fn(&str) -> String) {
    for (i, case) in load(name).into_iter().enumerate() {
        let input = serde_json::to_string(&case["input"]).unwrap();
        let got: Value = serde_json::from_str(&f(&input)).unwrap();
        assert_eq!(
            got, case["expected"],
            "{name}[{i}] mismatch\n input={input}"
        );
    }
}

#[test]
fn vec_resolve() {
    run("resolve", resolve_json);
}
#[test]
fn vec_apply() {
    run("apply_decision", apply_decision_json);
}
#[test]
fn vec_evaluate() {
    run("evaluate_builtin", evaluate_builtin_json);
}
#[test]
fn vec_auto_config() {
    run("auto_config", auto_config_json);
}
#[test]
fn vec_skip_if() {
    run("skip_if", skip_if_falsy_json);
}
#[test]
fn vec_plan_pipeline() {
    run("plan_pipeline", plan_pipeline_json);
}
#[test]
fn vec_apply_scale_hints() {
    run("apply_scale_hints", apply_scale_hints_json);
}
#[test]
fn vec_band_of() {
    run("band_of", band_of_json);
}
