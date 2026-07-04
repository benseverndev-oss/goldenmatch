//! resolve(config, stage_info[]) -> ExecutionPlan | PlanError. Mirrors resolver.py:37-73.
use std::collections::BTreeSet;

use crate::model::{ExecutionPlan, PipelineConfig, PlanError, PlannedSpec, StageInfo};

pub fn resolve(config: &PipelineConfig, stages: &[StageInfo]) -> Result<ExecutionPlan, PlanError> {
    let by_key = |k: &str| stages.iter().find(|s| s.key == k);

    let mut plan = ExecutionPlan { stages: vec![] };
    let mut available: BTreeSet<String> = BTreeSet::new();

    // Auto-prepend `load` iff a stage is registered under key "load"; else seed "df".
    // Python hardcodes the LITERAL name "load" (resolver.py:42), NOT load.info.name —
    // reproduce that (a load stage whose info.name != "load" must still plan as "load").
    if let Some(load) = by_key("load") {
        plan.stages.push(PlannedSpec {
            name: "load".into(),
            use_: "load".into(),
            config: Default::default(),
            skip_if: None,
            on_error: Default::default(),
        });
        available.extend(load.produces.iter().cloned());
    } else {
        available.insert("df".into());
    }

    for entry in &config.stages {
        let spec = entry.clone().into_spec();
        let info = by_key(&spec.use_).ok_or(PlanError::UnknownStage {
            use_: spec.use_.clone(),
        })?;
        let name = spec.name.clone().unwrap_or_else(|| info.name.clone());

        for dep in &info.consumes {
            if !available.contains(dep) {
                return Err(PlanError::Wiring {
                    stage: name,
                    missing: dep.clone(),
                    available: available.iter().cloned().collect(), // BTreeSet -> sorted Vec
                });
            }
        }
        plan.stages.push(PlannedSpec {
            name,
            use_: spec.use_,
            config: spec.config,
            skip_if: spec.skip_if,
            on_error: spec.on_error,
        });
        available.extend(info.produces.iter().cloned());
    }
    Ok(plan)
}

#[cfg(test)]
mod tests {
    use super::resolve;
    use crate::model::*;

    fn info(key: &str, produces: &[&str], consumes: &[&str]) -> StageInfo {
        StageInfo {
            key: key.into(),
            name: key.into(),
            produces: produces.iter().map(|s| s.to_string()).collect(),
            consumes: consumes.iter().map(|s| s.to_string()).collect(),
        }
    }
    fn cfg(stages: Vec<StageEntry>) -> PipelineConfig {
        PipelineConfig {
            pipeline: "auto".into(),
            source: None,
            output: None,
            stages,
            decisions: vec![],
        }
    }
    fn name_entry(u: &str) -> StageEntry {
        StageEntry::Name(u.into())
    }

    #[test]
    fn happy_order_and_auto_prepend_load() {
        let stages = vec![
            info("load", &["df"], &[]),
            info("goldencheck.scan", &["findings"], &["df"]),
            info("goldenmatch.dedupe", &["clusters"], &["df", "findings"]),
        ];
        let plan = resolve(
            &cfg(vec![
                name_entry("goldencheck.scan"),
                name_entry("goldenmatch.dedupe"),
            ]),
            &stages,
        )
        .unwrap();
        let names: Vec<_> = plan.stages.iter().map(|s| s.name.as_str()).collect();
        assert_eq!(names, ["load", "goldencheck.scan", "goldenmatch.dedupe"]);
    }

    #[test]
    fn no_load_seeds_df() {
        let stages = vec![info("s", &["out"], &["df"])];
        let plan = resolve(&cfg(vec![name_entry("s")]), &stages).unwrap();
        assert_eq!(plan.stages.len(), 1); // df available even with no load stage
    }

    #[test]
    fn wiring_error_lists_sorted_available() {
        let stages = vec![info("s", &["out"], &["missing"])];
        let err = resolve(&cfg(vec![name_entry("s")]), &stages).unwrap_err();
        match err {
            PlanError::Wiring {
                stage,
                missing,
                available,
            } => {
                assert_eq!(stage, "s");
                assert_eq!(missing, "missing");
                assert_eq!(available, vec!["df".to_string()]); // sorted
            }
            _ => panic!("expected Wiring"),
        }
    }

    #[test]
    fn unknown_use_is_unknown_stage() {
        let err = resolve(&cfg(vec![name_entry("nope")]), &[]).unwrap_err();
        assert_eq!(
            err,
            PlanError::UnknownStage {
                use_: "nope".into()
            }
        );
    }

    #[test]
    fn planned_name_prefers_spec_name_over_info_name() {
        let stages = vec![info("thekey", &[], &["df"])]; // info.name == "thekey"
        let spec = StageSpec {
            name: Some("alias".into()),
            use_: "thekey".into(),
            needs: vec![],
            skip_if: None,
            on_error: OnError::Continue,
            config: JsonMap::new(),
        };
        let plan = resolve(&cfg(vec![StageEntry::Spec(spec)]), &stages).unwrap();
        assert_eq!(plan.stages[0].name, "alias"); // spec.name wins
        assert_eq!(plan.stages[0].use_, "thekey");
    }

    #[test]
    fn lookup_by_key_not_name() {
        // key ("gm.dedupe") differs from info.name ("Dedupe"); config references the KEY
        let mut i = info("gm.dedupe", &[], &["df"]);
        i.name = "Dedupe".into();
        let plan = resolve(&cfg(vec![name_entry("gm.dedupe")]), &[i]).unwrap();
        assert_eq!(plan.stages[0].name, "Dedupe"); // fell back to info.name
    }

    #[test]
    fn auto_load_name_is_literal_not_info_name() {
        // a load stage whose info.name differs from its key must STILL plan as "load"
        let mut load = info("load", &["df"], &[]);
        load.name = "Loader".into();
        let plan = resolve(&cfg(vec![]), &[load]).unwrap();
        assert_eq!(plan.stages[0].name, "load"); // literal, per resolver.py:42
    }
}
