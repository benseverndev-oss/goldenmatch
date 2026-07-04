//! Serde structs mirroring goldenpipe's Python/TS models. Only the JSON-serializable
//! subset crosses the boundary; `config_schema` (a Python type) and the polars `df`
//! never enter the core.
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

pub type JsonMap = Map<String, Value>;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum OnError {
    #[default]
    Continue,
    Abort,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct StageSpec {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(rename = "use")]
    pub use_: String,
    #[serde(default)]
    pub needs: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub skip_if: Option<String>,
    #[serde(default)]
    pub on_error: OnError,
    #[serde(default)]
    pub config: JsonMap,
}

/// A `stages` entry is EITHER a full StageSpec OR a bare `use` string.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum StageEntry {
    Spec(StageSpec),
    Name(String),
}
impl StageEntry {
    /// Normalize to a StageSpec (bare string -> StageSpec{use: s}) — the makeStageSpec rule.
    pub fn into_spec(self) -> StageSpec {
        match self {
            StageEntry::Spec(s) => s,
            StageEntry::Name(s) => StageSpec {
                name: None,
                use_: s,
                needs: vec![],
                skip_if: None,
                on_error: OnError::Continue,
                config: JsonMap::new(),
            },
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PipelineConfig {
    pub pipeline: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub output: Option<String>,
    pub stages: Vec<StageEntry>,
    #[serde(default)]
    pub decisions: Vec<String>,
}

/// Registry metadata. `key` = the registration key the config's `use` references
/// (Python entry-point discovery keys by `ep.name`); `name` = `info.name`. They CAN
/// differ, so the core keys lookups by `key`.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct StageInfo {
    pub key: String,
    pub name: String,
    pub produces: Vec<String>,
    pub consumes: Vec<String>,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Decision {
    #[serde(default)]
    pub skip: Vec<String>,
    #[serde(default)]
    pub abort: bool,
    #[serde(default)]
    pub insert: Vec<String>,
    #[serde(default)]
    pub reason: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PlannedSpec {
    pub name: String,
    #[serde(rename = "use")]
    pub use_: String,
    // config/skip_if/on_error carry `default` so a PlannedSpec SERIALIZED by resolve
    // (which omits skip_if via skip_serializing_if, and could omit others) round-trips
    // when the host feeds the plan's stages straight into apply_decision. Without this
    // the resolve->apply_decision handoff would fail to deserialize (an SP2/SP3 bug).
    #[serde(default)]
    pub config: JsonMap,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub skip_if: Option<String>,
    #[serde(default)]
    pub on_error: OnError,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ExecutionPlan {
    pub stages: Vec<PlannedSpec>,
}

/// Tagged union of the planner's failure classes.
/// - `MissingProducer`: a consumed artifact no stage (nor the `df` seed) produces.
/// - `AmbiguousProducer`: an unsatisfied consumer with >=2 later producers, no `needs` tiebreak.
/// - `Cycle`: the declared edges (`needs` + sole-producer) contain a cycle.
/// - `UnknownNeed`: a `needs` entry naming a stage/key not in the pipeline.
/// - `UnknownStage`: a `use` with no registered stage.
///
/// The error `stage` field carries the PLANNED NAME (`spec.name or info.name`), matching
/// the pre-DAG `Wiring` error; only `available` was dropped.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum PlanError {
    MissingProducer {
        stage: String,
        artifact: String,
    },
    AmbiguousProducer {
        artifact: String,
        producers: Vec<String>,
    },
    Cycle {
        stages: Vec<String>,
    },
    UnknownNeed {
        stage: String,
        needs: Vec<String>,
    },
    UnknownStage {
        #[serde(rename = "use")]
        use_: String,
    },
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct CtxSubset {
    #[serde(default)]
    pub artifacts: JsonMap,
    #[serde(default)]
    pub metadata: JsonMap,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ApplyResult {
    pub remaining: Vec<PlannedSpec>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub router_note: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bare_string_stage_entry_normalizes() {
        let e: StageEntry = serde_json::from_str("\"goldencheck.scan\"").unwrap();
        let s = e.into_spec();
        assert_eq!(s.use_, "goldencheck.scan");
        assert_eq!(s.on_error, OnError::Continue);
    }

    #[test]
    fn stagespec_uses_serde_rename_use() {
        let s: StageSpec = serde_json::from_str(r#"{"use":"x"}"#).unwrap();
        assert_eq!(s.use_, "x");
        // round-trips back to "use", not "use_"
        assert!(serde_json::to_string(&s).unwrap().contains("\"use\":\"x\""));
    }

    #[test]
    fn on_error_defaults_continue_and_lowercases() {
        assert_eq!(OnError::default(), OnError::Continue);
        let v = serde_json::to_string(&OnError::Abort).unwrap();
        assert_eq!(v, "\"abort\"");
    }

    #[test]
    fn plan_error_new_variants_serialize_with_kind_tag() {
        let ambig = PlanError::AmbiguousProducer {
            artifact: "df".into(),
            producers: vec!["a".into(), "b".into()],
        };
        assert_eq!(
            serde_json::to_value(&ambig).unwrap(),
            serde_json::json!({"kind": "ambiguous_producer", "artifact": "df", "producers": ["a", "b"]})
        );
        let cyc = PlanError::Cycle {
            stages: vec!["a".into(), "b".into()],
        };
        assert_eq!(
            serde_json::to_value(&cyc).unwrap(),
            serde_json::json!({"kind": "cycle", "stages": ["a", "b"]})
        );
        let un = PlanError::UnknownNeed {
            stage: "s".into(),
            needs: vec!["ghost".into()],
        };
        assert_eq!(
            serde_json::to_value(&un).unwrap(),
            serde_json::json!({"kind": "unknown_need", "stage": "s", "needs": ["ghost"]})
        );
        let mp = PlanError::MissingProducer {
            stage: "s".into(),
            artifact: "x".into(),
        };
        assert_eq!(
            serde_json::to_value(&mp).unwrap(),
            serde_json::json!({"kind": "missing_producer", "stage": "s", "artifact": "x"})
        );
    }
}
