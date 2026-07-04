//! Serde structs mirroring goldenpipe's Python/TS models. Only the JSON-serializable
//! subset crosses the boundary; `config_schema` (a Python type) and the polars `df`
//! never enter the core.
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

pub type JsonMap = Map<String, Value>;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum OnError {
    Continue,
    Abort,
}
impl Default for OnError {
    fn default() -> Self {
        OnError::Continue
    }
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
    pub config: JsonMap,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub skip_if: Option<String>,
    pub on_error: OnError,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ExecutionPlan {
    pub stages: Vec<PlannedSpec>,
}

/// Tagged union preserving goldenpipe's TWO error classes: `Wiring` (a consume not
/// produced by an earlier stage) and `UnknownStage` (a `use` with no registered stage).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum PlanError {
    Wiring {
        stage: String,
        missing: String,
        available: Vec<String>,
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
}
