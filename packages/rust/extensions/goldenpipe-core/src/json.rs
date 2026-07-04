//! JSON wrappers: the surface the native/wasm shims call and the golden-vector harness
//! replays. Each parses its input struct, calls the typed fn, serializes the result.
//! Import list is exactly what's NAMED below (no unused imports -> zero-warning build):
//! the In structs derive only Deserialize; ExecutionPlan/PlanError/ApplyResult are
//! RETURNED but never named (serialized via json!/to_string), so they are NOT imported.
use serde::Deserialize;
use serde_json::{json, Value};

use crate::config::{auto_config, skip_if_falsy};
use crate::decisions::evaluate_builtin;
use crate::model::{CtxSubset, Decision, JsonMap, PipelineConfig, PlannedSpec, StageInfo};
use crate::resolve::resolve;
use crate::router::apply_decision;

fn parse_err(e: impl std::fmt::Display) -> String {
    json!({"err": {"kind": "parse", "msg": e.to_string()}}).to_string()
}

#[derive(Deserialize)]
struct ResolveIn {
    config: PipelineConfig,
    stages: Vec<StageInfo>,
}

pub fn resolve_json(input: &str) -> String {
    let arg: ResolveIn = match serde_json::from_str(input) {
        Ok(a) => a,
        Err(e) => return parse_err(e),
    };
    match resolve(&arg.config, &arg.stages) {
        Ok(plan) => json!({ "ok": plan }).to_string(),
        Err(err) => json!({ "err": err }).to_string(), // PlanError serializes with its "kind" tag
    }
}

#[derive(Deserialize)]
struct ApplyIn {
    decision: Decision,
    remaining: Vec<PlannedSpec>,
}

pub fn apply_decision_json(input: &str) -> String {
    let arg: ApplyIn = match serde_json::from_str(input) {
        Ok(a) => a,
        Err(e) => return parse_err(e),
    };
    serde_json::to_string(&apply_decision(&arg.decision, &arg.remaining)).unwrap()
}

#[derive(Deserialize)]
struct EvalIn {
    name: String,
    ctx: CtxSubset,
}

pub fn evaluate_builtin_json(input: &str) -> String {
    let arg: EvalIn = match serde_json::from_str(input) {
        Ok(a) => a,
        Err(e) => return parse_err(e),
    };
    // None serializes to JSON null (the "no decision" signal).
    serde_json::to_string(&evaluate_builtin(&arg.name, &arg.ctx)).unwrap()
}

#[derive(Deserialize)]
struct AutoIn {
    available: Vec<String>,
    #[serde(default)]
    identity_opts: Option<JsonMap>,
}

pub fn auto_config_json(input: &str) -> String {
    let arg: AutoIn = match serde_json::from_str(input) {
        Ok(a) => a,
        Err(e) => return parse_err(e),
    };
    serde_json::to_string(&auto_config(&arg.available, arg.identity_opts.as_ref())).unwrap()
}

pub fn skip_if_falsy_json(input: &str) -> String {
    let v: Value = match serde_json::from_str(input) {
        Ok(a) => a,
        Err(e) => return parse_err(e),
    };
    skip_if_falsy(&v).to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v(s: &str) -> Value {
        serde_json::from_str(s).unwrap()
    }

    #[test]
    fn resolve_json_ok_and_err_shapes() {
        let ok = resolve_json(
            r#"{"config":{"pipeline":"auto","stages":["s"]},
                "stages":[{"key":"s","name":"s","produces":[],"consumes":["df"]}]}"#,
        );
        assert_eq!(v(&ok)["ok"]["stages"][0]["name"], "s");

        let err = resolve_json(r#"{"config":{"pipeline":"auto","stages":["nope"]},"stages":[]}"#);
        assert_eq!(v(&err)["err"]["kind"], "unknown_stage");
        assert_eq!(v(&err)["err"]["use"], "nope");
    }

    #[test]
    fn resolve_json_config_echoes_insertion_order() {
        // config keys z,a,m must ROUND-TRIP in that order (preserve_order), not sorted.
        let out = resolve_json(
            r#"{"config":{"pipeline":"auto","stages":[{"use":"s","config":{"z":1,"a":2,"m":3}}]},
                "stages":[{"key":"s","name":"s","produces":[],"consumes":["df"]}]}"#,
        );
        let cfg_str = out.split("\"config\":{").nth(1).unwrap();
        assert!(
            cfg_str.starts_with("\"z\":1,\"a\":2,\"m\":3"),
            "got {cfg_str}"
        );
    }

    #[test]
    fn parse_error_is_tagged() {
        let out = resolve_json("{not json");
        assert_eq!(v(&out)["err"]["kind"], "parse");
    }

    #[test]
    fn skip_if_falsy_json_roundtrips() {
        assert_eq!(skip_if_falsy_json("{}"), "true");
        assert_eq!(skip_if_falsy_json("{\"a\":1}"), "false");
    }
}
