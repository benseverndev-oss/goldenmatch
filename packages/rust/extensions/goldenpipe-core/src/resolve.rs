//! resolve(config, stage_info[]) -> ExecutionPlan | PlanError.
//! Dependency-DAG planner (spec §3.1): config-order-authoritative with a virtual `df`
//! seed, `needs` + guarded sole-producer edges, and a stable Kahn topo-sort. An
//! already-valid pipeline produces zero edges -> byte-identical to config order.
use std::cmp::Reverse;
use std::collections::{BTreeSet, BinaryHeap};

use crate::model::{ExecutionPlan, PipelineConfig, PlanError, PlannedSpec, StageInfo};

struct Node<'a> {
    pname: String,
    use_: String,
    info: &'a StageInfo,
    needs: Vec<String>,
    spec: PlannedSpec,
}

pub fn resolve(config: &PipelineConfig, stages: &[StageInfo]) -> Result<ExecutionPlan, PlanError> {
    let by_key = |k: &str| stages.iter().find(|s| s.key == k);

    // 1. Build the ordered node list (load prepended per SP1; UnknownStage on bad `use`).
    let mut nodes: Vec<Node> = Vec::new();
    let has_load = by_key("load").is_some();
    if let Some(load) = by_key("load") {
        nodes.push(Node {
            pname: "load".into(),
            use_: "load".into(),
            info: load,
            needs: vec![],
            spec: PlannedSpec {
                name: "load".into(),
                use_: "load".into(),
                config: Default::default(),
                skip_if: None,
                on_error: Default::default(),
            },
        });
    }
    for entry in &config.stages {
        let spec = entry.clone().into_spec();
        let info = by_key(&spec.use_).ok_or(PlanError::UnknownStage {
            use_: spec.use_.clone(),
        })?;
        let pname = spec.name.clone().unwrap_or_else(|| info.name.clone());
        nodes.push(Node {
            pname: pname.clone(),
            use_: spec.use_.clone(),
            info,
            needs: spec.needs.clone(),
            spec: PlannedSpec {
                name: pname,
                use_: spec.use_,
                config: spec.config,
                skip_if: spec.skip_if,
                on_error: spec.on_error,
            },
        });
    }
    let n = nodes.len();
    let seed_df = !has_load; // df seeded iff no load stage produces it at index 0

    // First node (by config index) whose `use` == k — the match key space for needs/producers.
    let key_to_idx = |k: &str| nodes.iter().position(|nd| nd.use_ == k);

    let mut edges: BTreeSet<(usize, usize)> = BTreeSet::new();

    // 2. needs edges (reported before missing/ambiguous — phase order).
    for (i, nd) in nodes.iter().enumerate() {
        for need in &nd.needs {
            match key_to_idx(need) {
                None => {
                    return Err(PlanError::UnknownNeed {
                        stage: nd.pname.clone(),
                        needs: vec![need.clone()],
                    })
                }
                Some(j) => {
                    edges.insert((j, i)); // self-edge (j==i) kept -> caught as Cycle in step 4
                }
            }
        }
    }

    // 3. Guarded sole-producer edges. First violation (by config index, then consumes order) wins.
    let produced_before = |i: usize, x: &str| -> bool {
        (seed_df && x == "df")
            || nodes[..i]
                .iter()
                .any(|nd| nd.info.produces.iter().any(|p| p == x))
    };
    for i in 0..n {
        for dep in &nodes[i].info.consumes {
            if produced_before(i, dep) {
                continue; // satisfied by seed or an earlier stage -> no edge, no error
            }
            let later: Vec<usize> = ((i + 1)..n)
                .filter(|&j| nodes[j].info.produces.iter().any(|p| p == dep))
                .collect();
            match later.len() {
                0 => {
                    return Err(PlanError::MissingProducer {
                        stage: nodes[i].pname.clone(),
                        artifact: dep.clone(),
                    })
                }
                1 => {
                    edges.insert((later[0], i));
                }
                _ => {
                    // A producer is "pinned" if ANY must-precede edge already forces it
                    // before this consumer -- a `needs` edge OR a sole-producer edge added
                    // for an earlier `consumes` entry of this same stage `i`. Exactly one
                    // pinned => deterministic binding (a legal re-production chain), so it
                    // resolves; zero or >=2 pinned => AmbiguousProducer (spec §3.1 rule 2).
                    let pinned = later.iter().filter(|&&j| edges.contains(&(j, i))).count();
                    if pinned != 1 {
                        return Err(PlanError::AmbiguousProducer {
                            artifact: dep.clone(),
                            producers: later.iter().map(|&j| nodes[j].use_.clone()).collect(),
                        });
                    }
                    // exactly one needs-pinned producer: it already has an edge; nothing to add.
                }
            }
        }
    }

    // 4. Stable Kahn topo-sort keyed by config index (min-heap of indices).
    let mut indeg = vec![0usize; n];
    let mut adj: Vec<Vec<usize>> = vec![vec![]; n];
    for &(a, b) in &edges {
        if a == b {
            // self-edge: bump in-degree so the node can never schedule; it (and any
            // other cycle members) fall through to the ascending-config-index report
            // below. Avoids depending on edge-set iteration order for the message.
            indeg[b] += 1;
            continue;
        }
        adj[a].push(b);
        indeg[b] += 1;
    }
    let mut heap: BinaryHeap<Reverse<usize>> = BinaryHeap::new();
    for (i, &d) in indeg.iter().enumerate() {
        if d == 0 {
            heap.push(Reverse(i));
        }
    }
    let mut order: Vec<usize> = Vec::with_capacity(n);
    while let Some(Reverse(u)) = heap.pop() {
        order.push(u);
        for &v in &adj[u] {
            indeg[v] -= 1;
            if indeg[v] == 0 {
                heap.push(Reverse(v));
            }
        }
    }
    if order.len() != n {
        let cyc: Vec<String> = (0..n)
            .filter(|&i| indeg[i] > 0)
            .map(|i| nodes[i].pname.clone())
            .collect();
        return Err(PlanError::Cycle { stages: cyc });
    }

    // 5. Emit in sorted order (zero edges -> 0..n -> config order, byte-identical).
    Ok(ExecutionPlan {
        stages: order.into_iter().map(|i| nodes[i].spec.clone()).collect(),
    })
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
    fn spec_entry(u: &str, needs: &[&str]) -> StageEntry {
        StageEntry::Spec(StageSpec {
            name: None,
            use_: u.into(),
            needs: needs.iter().map(|s| s.to_string()).collect(),
            skip_if: None,
            on_error: OnError::Continue,
            config: JsonMap::new(),
        })
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
    fn missing_producer_when_no_stage_produces_dep() {
        let stages = vec![info("s", &["out"], &["missing"])];
        let err = resolve(&cfg(vec![name_entry("s")]), &stages).unwrap_err();
        assert_eq!(
            err,
            PlanError::MissingProducer {
                stage: "s".into(),
                artifact: "missing".into(),
            }
        );
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

    #[test]
    fn already_valid_pipeline_is_byte_identical() {
        let stages = vec![
            info("load", &["df"], &[]),
            info("goldenflow.transform", &["df", "manifest"], &["df"]),
            info("goldenmatch.dedupe", &["clusters"], &["df"]),
        ];
        let plan = resolve(
            &cfg(vec![
                name_entry("goldenflow.transform"),
                name_entry("goldenmatch.dedupe"),
            ]),
            &stages,
        )
        .unwrap();
        let names: Vec<_> = plan.stages.iter().map(|s| s.name.as_str()).collect();
        assert_eq!(
            names,
            ["load", "goldenflow.transform", "goldenmatch.dedupe"]
        );
    }

    #[test]
    fn reorders_consumer_before_its_sole_producer() {
        let stages = vec![info("a", &["out"], &["df"]), info("b", &[], &["out", "df"])];
        let plan = resolve(&cfg(vec![name_entry("b"), name_entry("a")]), &stages).unwrap();
        let names: Vec<_> = plan.stages.iter().map(|s| s.name.as_str()).collect();
        assert_eq!(names, ["a", "b"]);
    }

    #[test]
    fn needs_reorders_against_config_order() {
        let stages = vec![info("a", &[], &["df"]), info("b", &[], &["df"])];
        let plan = resolve(
            &cfg(vec![spec_entry("b", &["a"]), spec_entry("a", &[])]),
            &stages,
        )
        .unwrap();
        let names: Vec<_> = plan.stages.iter().map(|s| s.name.as_str()).collect();
        assert_eq!(names, ["a", "b"]);
    }

    #[test]
    fn reproduction_chain_stays_config_order() {
        let stages = vec![
            info("load", &["df"], &[]),
            info("t1", &["df"], &["df"]),
            info("t2", &["df"], &["df"]),
        ];
        let plan = resolve(&cfg(vec![name_entry("t1"), name_entry("t2")]), &stages).unwrap();
        let names: Vec<_> = plan.stages.iter().map(|s| s.name.as_str()).collect();
        assert_eq!(names, ["load", "t1", "t2"]);
    }

    #[test]
    fn missing_producer_when_absent_and_unseeded() {
        let stages = vec![info("s", &["out"], &["ghost"])];
        let err = resolve(&cfg(vec![name_entry("s")]), &stages).unwrap_err();
        assert_eq!(
            err,
            PlanError::MissingProducer {
                stage: "s".into(),
                artifact: "ghost".into()
            }
        );
    }

    #[test]
    fn ambiguous_producer_two_later_producers_no_needs() {
        let stages = vec![
            info("c", &[], &["x", "df"]),
            info("p1", &["x"], &["df"]),
            info("p2", &["x"], &["df"]),
        ];
        let err = resolve(
            &cfg(vec![name_entry("c"), name_entry("p1"), name_entry("p2")]),
            &stages,
        )
        .unwrap_err();
        assert_eq!(
            err,
            PlanError::AmbiguousProducer {
                artifact: "x".into(),
                producers: vec!["p1".into(), "p2".into()]
            }
        );
    }

    #[test]
    fn ambiguous_resolved_when_needs_pins_exactly_one() {
        let stages = vec![
            info("c", &[], &["x", "df"]),
            info("p1", &["x"], &["df"]),
            info("p2", &["x"], &["df"]),
        ];
        let plan = resolve(
            &cfg(vec![
                spec_entry("c", &["p2"]),
                name_entry("p1"),
                name_entry("p2"),
            ]),
            &stages,
        )
        .unwrap();
        let names: Vec<_> = plan.stages.iter().map(|s| s.name.as_str()).collect();
        // Stable Kahn by config index: p1@1 is free + lowest index -> schedules first;
        // p2@2 is free next and frees c@0 (the sole needs edge p2->c). Order = [p1, p2, c].
        assert_eq!(names, ["p1", "p2", "c"]);
    }

    #[test]
    fn cycle_when_needs_are_mutual() {
        let stages = vec![info("a", &[], &["df"]), info("b", &[], &["df"])];
        let err = resolve(
            &cfg(vec![spec_entry("a", &["b"]), spec_entry("b", &["a"])]),
            &stages,
        )
        .unwrap_err();
        assert_eq!(
            err,
            PlanError::Cycle {
                stages: vec!["a".into(), "b".into()]
            }
        );
    }

    #[test]
    fn unknown_need_names_absent_stage() {
        let stages = vec![info("s", &[], &["df"])];
        let err = resolve(&cfg(vec![spec_entry("s", &["ghost"])]), &stages).unwrap_err();
        assert_eq!(
            err,
            PlanError::UnknownNeed {
                stage: "s".into(),
                needs: vec!["ghost".into()]
            }
        );
    }

    #[test]
    fn resolve_is_deterministic_across_runs() {
        let stages = vec![info("a", &["out"], &["df"]), info("b", &[], &["out", "df"])];
        let c = cfg(vec![name_entry("b"), name_entry("a")]);
        assert_eq!(resolve(&c, &stages).unwrap(), resolve(&c, &stages).unwrap());
    }

    #[test]
    fn sole_producer_edge_pins_one_of_multiple_producers() {
        // i consumes Y and X; j (sole Y producer) is forced before i by the Y edge; k
        // re-produces X later. i binds j's X deterministically -> resolves, not ambiguous.
        let stages = vec![
            info("i", &[], &["y", "x", "df"]),
            info("j", &["y", "x"], &["df"]),
            info("k", &["x"], &["df"]),
        ];
        let plan = resolve(
            &cfg(vec![name_entry("i"), name_entry("j"), name_entry("k")]),
            &stages,
        )
        .unwrap();
        let names: Vec<_> = plan.stages.iter().map(|s| s.name.as_str()).collect();
        assert_eq!(names, ["j", "i", "k"]);
    }
}
