//! GoldenPipe auto-config BRAIN (the decision core), ported from
//! autoconfig_planner.py + autoconfig_planner_rules.py. Pure; the pure-Python
//! brain is the non-authoritative fallback proven to reproduce these bytes.
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::model::JsonMap;

const RED_NULL_DENSITY: f64 = 0.6;
const CONFIDENT_DOMAIN_THRESHOLD: f64 = 0.5;
const SCALE_ROUTE_MIN_ROWS: i64 = 1_000_000;
const THROUGHPUT_RECALL_TARGET: f64 = 0.95;
const GREEN_THRESHOLD: f64 = 0.7;
const AMBER_THRESHOLD: f64 = 0.4;

#[derive(Deserialize)]
pub struct PipeProfile {
    pub n_rows: i64,
    pub n_cols: i64,
    pub column_names: Vec<String>,
    pub dtypes: Vec<String>,
    pub inferred_domain: Option<String>,
    pub domain_confidence: f64,
}

#[derive(Deserialize)]
pub struct ComplexityProfile {
    pub max_null_density: f64,
    pub mean_null_density: f64,
}

#[derive(Deserialize)]
pub struct PlannerInput {
    pub runtime: PipeProfile,
    pub complexity: ComplexityProfile,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct PlannedStage {
    pub name: String,
    pub config: JsonMap,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct PipePlan {
    pub stages: Vec<PlannedStage>,
    pub rule_name: String,
    pub confidence: f64,
    pub evidence: JsonMap,
}

pub fn band_of(confidence: f64) -> &'static str {
    if confidence >= GREEN_THRESHOLD {
        "green"
    } else if confidence >= AMBER_THRESHOLD {
        "amber"
    } else {
        "red"
    }
}

fn stage(name: &str, config: JsonMap) -> PlannedStage {
    PlannedStage { name: name.to_string(), config }
}

fn default_evidence(inp: &PlannerInput) -> JsonMap {
    // Insertion order MUST match Python default_evidence: n_rows, n_cols,
    // inferred_domain, domain_confidence, max_null_density, mean_null_density.
    let mut m = JsonMap::new();
    m.insert("n_rows".into(), json!(inp.runtime.n_rows));
    m.insert("n_cols".into(), json!(inp.runtime.n_cols));
    m.insert("inferred_domain".into(), json!(inp.runtime.inferred_domain));
    m.insert("domain_confidence".into(), json!(inp.runtime.domain_confidence));
    m.insert("max_null_density".into(), json!(inp.complexity.max_null_density));
    m.insert("mean_null_density".into(), json!(inp.complexity.mean_null_density));
    m
}

fn default_dedupe_stages() -> Vec<PlannedStage> {
    vec![
        stage("goldencheck.scan", JsonMap::new()),
        stage("goldenflow.transform", JsonMap::new()),
        stage("goldenmatch.dedupe", JsonMap::new()),
    ]
}

pub fn plan_pipeline(inp: &PlannerInput) -> PipePlan {
    let r = &inp.runtime;
    // 1. pathological
    if r.n_rows <= 1 {
        return PipePlan {
            stages: vec![
                stage("goldencheck.scan", JsonMap::new()),
                stage("goldenflow.transform", JsonMap::new()),
            ],
            rule_name: "pathological".into(),
            confidence: 1.0,
            evidence: default_evidence(inp),
        };
    }
    // 2. confident_schema
    if r.inferred_domain.is_some() && r.domain_confidence >= CONFIDENT_DOMAIN_THRESHOLD {
        let mut cfg = JsonMap::new();
        cfg.insert("domain".into(), json!(r.inferred_domain));
        return PipePlan {
            stages: vec![
                stage("infer_schema", cfg),
                stage("goldencheck.scan", JsonMap::new()),
                stage("goldenflow.transform", JsonMap::new()),
                stage("goldenmatch.dedupe", JsonMap::new()),
            ],
            rule_name: "confident_schema".into(),
            confidence: r.domain_confidence,
            evidence: default_evidence(inp),
        };
    }
    // 3. low_confidence (the sole RED source)
    if r.inferred_domain.is_none() && inp.complexity.max_null_density > RED_NULL_DENSITY {
        return PipePlan {
            stages: default_dedupe_stages(),
            rule_name: "low_confidence".into(),
            confidence: 0.3,
            evidence: default_evidence(inp),
        };
    }
    // 4. default
    PipePlan {
        stages: default_dedupe_stages(),
        rule_name: "default".into(),
        confidence: 0.7,
        evidence: default_evidence(inp),
    }
}

pub fn apply_scale_hints(plan: &PipePlan, runtime: &PipeProfile) -> PipePlan {
    if runtime.n_rows < SCALE_ROUTE_MIN_ROWS
        || !plan.stages.iter().any(|s| s.name == "goldenmatch.dedupe")
    {
        return plan.clone();
    }
    let stages = plan
        .stages
        .iter()
        .map(|s| {
            if s.name == "goldenmatch.dedupe" {
                let mut cfg = s.config.clone();
                cfg.insert(
                    "_dedupe_hints".into(),
                    json!({"throughput": {"recall_target": THROUGHPUT_RECALL_TARGET}}),
                );
                PlannedStage { name: s.name.clone(), config: cfg }
            } else {
                s.clone()
            }
        })
        .collect();
    let mut evidence = plan.evidence.clone();
    evidence.insert("scale_hinted".into(), Value::Bool(true));
    PipePlan {
        stages,
        rule_name: plan.rule_name.clone(),
        confidence: plan.confidence,
        evidence,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn inp(n_rows: i64, domain: Option<&str>, dc: f64, max_null: f64) -> PlannerInput {
        PlannerInput {
            runtime: PipeProfile {
                n_rows,
                n_cols: 2,
                column_names: vec!["a".into(), "b".into()],
                dtypes: vec!["String".into(), "String".into()],
                inferred_domain: domain.map(|s| s.to_string()),
                domain_confidence: dc,
            },
            complexity: ComplexityProfile { max_null_density: max_null, mean_null_density: 0.0 },
        }
    }

    #[test]
    fn band_thresholds() {
        assert_eq!(band_of(0.7), "green");
        assert_eq!(band_of(0.69), "amber");
        assert_eq!(band_of(0.39), "red");
    }

    #[test]
    fn rules_fire_in_order() {
        assert_eq!(plan_pipeline(&inp(1, None, 0.0, 0.0)).rule_name, "pathological");
        assert_eq!(plan_pipeline(&inp(100, Some("finance"), 0.8, 0.0)).rule_name, "confident_schema");
        assert_eq!(plan_pipeline(&inp(200000, None, 0.0, 0.7)).rule_name, "low_confidence");
        assert_eq!(plan_pipeline(&inp(100, None, 0.0, 0.0)).rule_name, "default");
        assert_eq!(plan_pipeline(&inp(100, Some("finance"), 0.4, 0.0)).rule_name, "default");
    }

    #[test]
    fn scale_hint_applies_and_noops() {
        let plan = plan_pipeline(&inp(100, None, 0.0, 0.0));
        let hinted = apply_scale_hints(&plan, &inp(1_000_000, None, 0.0, 0.0).runtime);
        assert_eq!(hinted.evidence.get("scale_hinted"), Some(&Value::Bool(true)));
        let below = apply_scale_hints(&plan, &inp(999_999, None, 0.0, 0.0).runtime);
        assert!(below.evidence.get("scale_hinted").is_none());
    }
}
