use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SuggestionKind {
    RaiseThreshold,
    LowerThreshold,
    SwapScorer,
    AddNegativeEvidence,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PredictedEffect { PrecisionUp, RecallUp }

/// Declarative config edit. The kernel defines WHAT to change once; each language
/// applies it to its own native config object.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum ConfigPatch {
    SetThreshold { matchkey: String, value: f64 },
    SetScorer { matchkey: String, field: String, scorer: String },
    AddNegativeEvidence { field: String },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Suggestion {
    pub id: String,
    pub kind: SuggestionKind,
    pub target: String,
    pub current_value: String,
    pub proposed_value: String,
    pub rationale: String,
    pub predicted_effect: PredictedEffect,
    pub confidence: f64,
    pub patch: ConfigPatch,
    pub evidence: serde_json::Value,
}

/// Reduced, frame-free view of the config (what the rules need to read).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConfigSummary {
    pub matchkeys: Vec<MatchkeySummary>,
    pub negative_evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MatchkeySummary {
    pub name: String,
    pub kind: String,            // "weighted" | "fuzzy" | "exact" | "probabilistic"
    pub threshold: Option<f64>,
    pub fields: Vec<FieldSummary>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FieldSummary {
    pub field: String,
    pub scorer: Option<String>,
    pub weight: Option<f64>,
}

/// Accept/reject history folded into ranking. Plan 2 fills this from MemoryStore;
/// Plan 1 always passes an empty map. key = "{snake_case kind}:{target}" -> (accepts, rejects).
/// The key is produced by `rank::prior_key` (Task 8) -- Plan 2's persistence MUST
/// use the same helper so the loop binds.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct AcceptancePriors {
    pub counts: std::collections::HashMap<String, (u32, u32)>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn suggestion_json_roundtrip() {
        let s = Suggestion {
            id: "thr:name:raise".into(),
            kind: SuggestionKind::RaiseThreshold,
            target: "name".into(),
            current_value: "0.80".into(),
            proposed_value: "0.88".into(),
            rationale: "placeholder".into(),
            predicted_effect: PredictedEffect::PrecisionUp,
            confidence: 0.7,
            patch: ConfigPatch::SetThreshold { matchkey: "name".into(), value: 0.88 },
            evidence: serde_json::json!({"dip": 0.86}),
        };
        let txt = serde_json::to_string(&s).unwrap();
        let back: Suggestion = serde_json::from_str(&txt).unwrap();
        assert_eq!(back.kind, SuggestionKind::RaiseThreshold);
        assert_eq!(back.patch, ConfigPatch::SetThreshold { matchkey: "name".into(), value: 0.88 });
    }
}
