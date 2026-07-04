//! auto_config + skip_if_falsy. Mirrors pipeline.py:75-89 and runner.py:30 (`not artifact`).
use serde_json::Value;

use crate::model::{JsonMap, OnError, PipelineConfig, StageEntry, StageSpec};

const DEFAULT_STAGES: [&str; 3] = [
    "goldencheck.scan",
    "goldenflow.transform",
    "goldenmatch.dedupe",
];
const IDENTITY: &str = "goldenmatch.identity_resolve";

pub fn auto_config(available: &[String], identity_opts: Option<&JsonMap>) -> PipelineConfig {
    let has = |name: &str| available.iter().any(|a| a == name);
    let mk = |use_: &str, config: JsonMap| {
        StageEntry::Spec(StageSpec {
            name: None,
            use_: use_.into(),
            needs: vec![],
            skip_if: None,
            on_error: OnError::Continue,
            config,
        })
    };

    let mut stages: Vec<StageEntry> = DEFAULT_STAGES
        .iter()
        .filter(|s| has(s))
        .map(|s| mk(s, JsonMap::new()))
        .collect();

    // Empty map == not-given (Python truthiness of a dict).
    if let Some(opts) = identity_opts {
        if !opts.is_empty() && has(IDENTITY) {
            stages.push(mk(IDENTITY, opts.clone()));
        }
    }
    PipelineConfig {
        pipeline: "auto".into(),
        source: None,
        output: None,
        stages,
        decisions: vec![],
    }
}

/// Canonical falsy predicate for the runner's `skip_if`. Python `not artifact` and TS
/// `isFalsy` agree on every JSON type; pinned here so they can't drift.
pub fn skip_if_falsy(artifact: &Value) -> bool {
    match artifact {
        Value::Null => true,
        Value::Bool(b) => !b,
        Value::Number(n) => n.as_f64().map(|x| x == 0.0).unwrap_or(false),
        Value::String(s) => s.is_empty(),
        Value::Array(a) => a.is_empty(),
        Value::Object(o) => o.is_empty(),
    }
}

#[cfg(test)]
mod tests {
    use super::{auto_config, skip_if_falsy};
    use crate::model::JsonMap;
    use serde_json::json;

    fn avail(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }
    fn uses(cfg: &crate::model::PipelineConfig) -> Vec<String> {
        cfg.stages
            .iter()
            .map(|e| e.clone().into_spec().use_)
            .collect()
    }

    #[test]
    fn all_available_default_three() {
        let c = auto_config(
            &avail(&[
                "goldencheck.scan",
                "goldenflow.transform",
                "goldenmatch.dedupe",
            ]),
            None,
        );
        assert_eq!(
            uses(&c),
            [
                "goldencheck.scan",
                "goldenflow.transform",
                "goldenmatch.dedupe"
            ]
        );
        assert_eq!(c.pipeline, "auto");
    }

    #[test]
    fn subset_filters() {
        let c = auto_config(&avail(&["goldenmatch.dedupe"]), None);
        assert_eq!(uses(&c), ["goldenmatch.dedupe"]);
    }

    #[test]
    fn identity_appended_when_nonempty_and_available() {
        let mut opts = JsonMap::new();
        opts.insert("threshold".into(), json!(0.8));
        let c = auto_config(
            &avail(&["goldenmatch.dedupe", "goldenmatch.identity_resolve"]),
            Some(&opts),
        );
        assert_eq!(
            uses(&c),
            ["goldenmatch.dedupe", "goldenmatch.identity_resolve"]
        );
    }

    #[test]
    fn identity_unavailable_not_appended() {
        let mut opts = JsonMap::new();
        opts.insert("t".into(), json!(1));
        let c = auto_config(&avail(&["goldenmatch.dedupe"]), Some(&opts));
        assert_eq!(uses(&c), ["goldenmatch.dedupe"]);
    }

    #[test]
    fn empty_opts_no_identity() {
        // Python `if self._identity_opts` treats {} as not-given.
        let c = auto_config(
            &avail(&["goldenmatch.dedupe", "goldenmatch.identity_resolve"]),
            Some(&JsonMap::new()),
        );
        assert_eq!(uses(&c), ["goldenmatch.dedupe"]);
    }

    #[test]
    fn skip_if_falsy_truth_table() {
        for t in [
            json!(null),
            json!(false),
            json!(0),
            json!(0.0),
            json!(""),
            json!([]),
            json!({}),
        ] {
            assert!(skip_if_falsy(&t), "{t:?} should be falsy");
        }
        for f in [
            json!(true),
            json!(1),
            json!(0.5),
            json!("x"),
            json!([0]),
            json!({"a":1}),
        ] {
            assert!(!skip_if_falsy(&f), "{f:?} should be truthy");
        }
    }
}
